from django.db import models
from django.contrib.postgres.fields import ArrayField


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
    direction = models.CharField(
        max_length=5, choices=Direction.choices, blank=True
    )
    start_position = models.DecimalField(
        max_digits=30, decimal_places=10, null=True, blank=True
    )
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
    liquidation_price = models.DecimalField(
        max_digits=30, decimal_places=10, null=True, blank=True
    )
    unrealized_pnl = models.DecimalField(
        max_digits=30, decimal_places=10, null=True, blank=True
    )
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
