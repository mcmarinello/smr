"""
Copy Trading (Paper Simulation) — PRD §Sprint 8.

V1 is paper-only: no real orders are ever sent. The models below capture
the user's virtual copy configuration (CopyTradingProfile), the wallets
that participate in a given profile (CopyTradingTarget), and the
simulated trades opened/closed against the profile's virtual capital
(SimulatedTrade).

Every SimulatedTrade references the original Hyperliquid Fill that
triggered it via `fill_source` so the simulation stays auditable and
re-runnable from the raw fills (CLAUDE.md: "todo score recalculável a
partir do fill bruto").
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.models import User
from wallets.models import BaseModel, Fill, Wallet


class CopyTradingProfile(BaseModel):
    """
    A user's paper-copy configuration. Owns the virtual capital allocation
    strategy and the per-trade size cap (max_position_pct = % of virtual
    capital the profile is willing to commit to a single simulated trade).
    """

    class Strategy(models.TextChoices):
        CONSERVATIVE = "conservative", "Conservative"
        MODERATE = "moderate", "Moderate"
        AGGRESSIVE = "aggressive", "Aggressive"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="copy_profiles",
    )
    name = models.CharField(max_length=120)
    # V1 paper baseline — every profile starts with this virtual capital.
    # PRD §Sprint 8: "simular 'e se eu tivesse copiado'".
    initial_capital = models.DecimalField(
        max_digits=30,
        decimal_places=10,
        default=10000,
        validators=[MinValueValidator(0)],
    )
    strategy = models.CharField(
        max_length=12,
        choices=Strategy.choices,
        default=Strategy.MODERATE,
        db_index=True,
    )
    # Max fraction of the profile's virtual capital committed to a single
    # simulated trade (PRD §Sprint 8 — `max_position_pct`).
    max_position_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    max_concurrent_positions = models.IntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
    )
    # Max hold period in hours before auto_close_stale forces a flat
    # (PRD §Sprint 8 — configurable per profile; 0 disables the auto-close).
    max_hold_hours = models.IntegerField(
        default=168,
        validators=[MinValueValidator(0), MaxValueValidator(100000)],
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "copytrading_profile"
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["is_active"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.strategy}, {self.user.username})"


class CopyTradingTarget(BaseModel):
    """
    One row per wallet participating in a profile. `allocation_pct` is the
    share of the profile's virtual capital earmarked for this wallet — the
    simulator derives the per-trade notional from it (see simulator.py).
    """

    profile = models.ForeignKey(
        CopyTradingProfile,
        on_delete=models.CASCADE,
        related_name="targets",
    )
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="copy_targets",
    )
    allocation_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=25,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "copytrading_target"
        unique_together = ("profile", "wallet")
        indexes = [
            models.Index(fields=["profile", "is_active"]),
            models.Index(fields=["wallet", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.profile.name} → {self.wallet.address[:8]}"


class SimulatedTrade(BaseModel):
    """
    One paper-trade row opened by the simulator against a profile's virtual
    capital. `fill_source` is the original HL fill that triggered the open
    so the simulation can be replayed/audited from the raw fills alone
    (CLAUDE.md: every score/trade recalculável a partir do fill bruto).
    """

    class Side(models.TextChoices):
        LONG = "long", "Long"
        SHORT = "short", "Short"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        LIQUIDATED = "liquidated", "Liquidated"

    profile = models.ForeignKey(
        CopyTradingProfile,
        on_delete=models.CASCADE,
        related_name="trades",
    )
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="simulated_trades",
    )
    asset = models.CharField(max_length=20, db_index=True)
    side = models.CharField(max_length=5, choices=Side.choices, db_index=True)
    entry_price = models.DecimalField(max_digits=30, decimal_places=10)
    # size_usd = notional at open in USD (virtual).
    size_usd = models.DecimalField(max_digits=30, decimal_places=10)
    exit_price = models.DecimalField(
        max_digits=30, decimal_places=10, null=True, blank=True
    )
    # realized PnL in USD; NULL while the trade is still open.
    pnl_usd = models.DecimalField(
        max_digits=30, decimal_places=10, null=True, blank=True
    )
    opened_at = models.DateTimeField(db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    # Original HL fill that triggered this open. Nullable so the simulator
    # can also record closes that arrived without a tracked counterpart.
    # db_constraint=False: wallets_fill's primary key becomes a composite
    # (id, timestamp) once it's a TimescaleDB hypertable (see wallets
    # migration 0003), so Postgres can no longer enforce a plain FK against
    # `id` alone. Django still tracks the relation at the ORM level.
    fill_source = models.ForeignKey(
        Fill,
        on_delete=models.SET_NULL,
        related_name="simulated_trades",
        null=True,
        blank=True,
        db_constraint=False,
    )

    class Meta:
        db_table = "copytrading_trade"
        indexes = [
            models.Index(fields=["profile", "status"]),
            models.Index(fields=["profile", "wallet", "status"]),
            models.Index(fields=["wallet", "asset", "status"]),
            models.Index(fields=["opened_at"]),
        ]
        ordering = ["-opened_at"]

    def __str__(self) -> str:
        return (
            f"{self.profile.name} {self.side} {self.size_usd} "
            f"{self.asset} @ {self.entry_price} [{self.status}]"
        )