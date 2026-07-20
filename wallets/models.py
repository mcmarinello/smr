from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Wallet(BaseModel):
    class DiscoverySource(models.TextChoices):
        LEADERBOARD = "leaderboard", "Leaderboard"
        TRADE_STREAM = "trade_stream", "Trade Stream"
        MANUAL = "manual", "Manual"

    address = models.CharField(max_length=42, unique=True, db_index=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    discovery_source = models.CharField(
        max_length=20, choices=DiscoverySource.choices, default=DiscoverySource.MANUAL
    )
    is_target = models.BooleanField(default=False, db_index=True)
    promoted_reason = models.TextField(blank=True)
    promoted_at = models.DateTimeField(null=True, blank=True)
    score_at_promotion = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    tags = ArrayField(models.CharField(max_length=100), default=list, blank=True)

    class Meta:
        db_table = "wallets_wallet"
        indexes = [
            models.Index(fields=["is_target", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.address


class Fill(BaseModel):
    """
    Source of truth for all score calculations. TimescaleDB hypertable on timestamp.
    Deduplication via oid (Hyperliquid order ID).
    """

    class Side(models.TextChoices):
        BUY = "buy", "Buy"
        SELL = "sell", "Sell"

    class Direction(models.TextChoices):
        OPEN = "open", "Open"
        CLOSE = "close", "Close"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="fills")
    asset = models.CharField(max_length=20, db_index=True)
    side = models.CharField(max_length=4, choices=Side.choices)
    price = models.DecimalField(max_digits=30, decimal_places=10)
    size = models.DecimalField(max_digits=30, decimal_places=10)
    fee = models.DecimalField(max_digits=30, decimal_places=10)
    closed_pnl = models.DecimalField(max_digits=30, decimal_places=10)
    timestamp = models.DateTimeField(db_index=True)
    is_liquidation = models.BooleanField(default=False)
    oid = models.BigIntegerField(db_index=True)
    direction = models.CharField(max_length=5, choices=Direction.choices, blank=True)
    start_position = models.DecimalField(max_digits=30, decimal_places=10, null=True, blank=True)
    hash = models.CharField(max_length=100, blank=True)
    tid = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "wallets_fill"
        indexes = [
            models.Index(fields=["wallet", "timestamp"]),
            models.Index(fields=["wallet", "asset", "timestamp"]),
            models.Index(fields=["oid"], name="fill_oid_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.wallet.address[:8]} {self.side} {self.size} {self.asset} @ {self.price}"


class Position(BaseModel):
    class Side(models.TextChoices):
        LONG = "long", "Long"
        SHORT = "short", "Short"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="positions")
    asset = models.CharField(max_length=20, db_index=True)
    side = models.CharField(max_length=5, choices=Side.choices)
    size = models.DecimalField(max_digits=30, decimal_places=10)
    entry_price = models.DecimalField(max_digits=30, decimal_places=10)
    leverage = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    liquidation_price = models.DecimalField(max_digits=30, decimal_places=10, null=True, blank=True)
    unrealized_pnl = models.DecimalField(max_digits=30, decimal_places=10, null=True, blank=True)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.OPEN)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "wallets_position"
        indexes = [
            models.Index(fields=["wallet", "status"]),
            models.Index(fields=["wallet", "asset", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.wallet.address[:8]} {self.side} {self.size} {self.asset} [{self.status}]"


class Window(models.TextChoices):
    """
    PRD §15.2 — every score is computed independently for each of these
    rolling windows so the dashboard can compare "hot now" (24h/7d) vs.
    long-term consistency (90d/180d). All-time is deferred to V2.
    """

    H24 = "24h", "24 Hours"
    D7 = "7d", "7 Days"
    D30 = "30d", "30 Days"
    D90 = "90d", "90 Days"
    D180 = "180d", "180 Days"


WINDOW_DAYS: dict[str, int] = {
    Window.H24.value: 1,
    Window.D7.value: 7,
    Window.D30.value: 30,
    Window.D90.value: 90,
    Window.D180.value: 180,
}


def days_for_window(window: str) -> int:
    """Map a PRD §15.2 window label to its duration in calendar days."""
    return WINDOW_DAYS[window]


class WalletMetricsWindow(BaseModel):
    """
    Frozen snapshot of the intermediate metrics (PRD §15.1 inputs) used to
    compute a WalletScore for a given window. Storing it makes the score
    auditable: every WalletScore can be re-derived from the raw fills plus
    the metrics stored here (PRD: "todo score recalculável a partir do fill
    bruto"). One row per (wallet, window).
    """

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="metrics_windows")
    window = models.CharField(max_length=5, choices=Window.choices, db_index=True)
    computed_at = models.DateTimeField(db_index=True)
    total_trades = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    total_pnl = models.DecimalField(max_digits=30, decimal_places=10, default=0)
    total_fees = models.DecimalField(max_digits=30, decimal_places=10, default=0)
    account_value = models.DecimalField(max_digits=30, decimal_places=10, default=0)
    normalized_pnl = models.DecimalField(max_digits=30, decimal_places=10, default=0)
    max_drawdown = models.DecimalField(max_digits=30, decimal_places=10, default=0)
    max_drawdown_pct = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    current_drawdown_pct = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    daily_returns_std = models.DecimalField(max_digits=20, decimal_places=10, default=0)
    avg_notional_ratio = models.DecimalField(max_digits=20, decimal_places=10, default=0)
    notional_ratio_std = models.DecimalField(max_digits=20, decimal_places=10, default=0)
    martingale_severity = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    martingale_events = models.IntegerField(default=0)
    assets_total_count = models.IntegerField(default=0)
    assets_positive_count = models.IntegerField(default=0)
    asset_pnl_json = models.JSONField(default=dict, blank=True)
    regime_pnl_json = models.JSONField(default=dict, blank=True)
    daily_returns_json = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "wallets_metrics_window"
        unique_together = ("wallet", "window")
        indexes = [
            models.Index(fields=["window", "computed_at"]),
        ]
        ordering = ["-computed_at"]

    def __str__(self) -> str:
        return f"{self.wallet.address[:8]} {self.window} metrics"


class WalletScore(BaseModel):
    """
    The raw base score (PRD §15.1) for a (wallet, window) pair plus the
    component breakdown for full auditability. `rank` is filled in
    after-the-fact by `_recompute_ranks` so the leaderboard view can sort
    wallets within each window.
    """

    class Classification(models.TextChoices):
        FRACO = "fraco", "Fraco"
        MEDIANO = "mediano", "Mediano"
        BOM = "bom", "Bom"
        ELITE = "elite", "Elite"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="scores")
    window = models.CharField(max_length=5, choices=Window.choices, db_index=True)
    computed_at = models.DateTimeField(db_index=True)
    score_raw = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    classification = models.CharField(
        max_length=10,
        choices=Classification.choices,
        default=Classification.FRACO,
        db_index=True,
    )
    component_breakdown = models.JSONField(default=dict, blank=True)
    rank = models.IntegerField(null=True, blank=True, db_index=True)
    metrics_window = models.OneToOneField(
        WalletMetricsWindow,
        on_delete=models.SET_NULL,
        related_name="score",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "wallets_score"
        unique_together = ("wallet", "window")
        indexes = [
            models.Index(fields=["window", "-score_raw"]),
            models.Index(fields=["classification", "window"]),
        ]
        ordering = ["-score_raw"]

    def __str__(self) -> str:
        return f"{self.wallet.address[:8]} {self.window} {self.score_raw}"

    @property
    def score_raw_float(self) -> float:
        return float(self.score_raw)
