"""
Alert Engine — PRD §17.

AlertRule     — user-configurable trigger; nullable wallet means "all targets".
Notification  — one row per fired event, surfaces the UI badge and the
                outbound delivery state (sent_at). Telegram integration is a
                Sprint 6 stretch goal; for V1 the dispatcher just logs.
AlertHistory  — append-only log of (rule, wallet, event_type, asset) firings,
                the dedup source of truth so we don't spam the same alert
                within the configured cooldown (settings.ALERT_DEDUP_COOLDOWN_SECONDS).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.models import User
from wallets.models import BaseModel, Wallet


class AlertRule(BaseModel):
    """PRD §17 — one configurable alert trigger owned by a user."""

    class ConditionType(models.TextChoices):
        NEW_POSITION = "new_position", "New position opened"
        POSITION_CLOSED = "position_closed", "Position closed/reduced"
        SCORE_THRESHOLD_CROSS = "score_threshold_cross", "Score crossed threshold"
        CONVERGENCE = "convergence", "Smart-money convergence"
        ASSET_SPECIFIC = "asset_specific", "Asset-specific filter"

    class Channel(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        INTERFACE = "interface", "Interface"
        BOTH = "both", "Telegram + Interface"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="alert_rules",
    )
    # PRD §17 — null wallet means "applies to every target wallet" (is_target).
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="alert_rules",
        null=True,
        blank=True,
        db_index=True,
    )
    condition_type = models.CharField(
        max_length=32,
        choices=ConditionType.choices,
        db_index=True,
    )
    # Comma-separated asset symbols ("BTC,ETH"). Empty/NULL = any asset.
    # Used as a filter by every condition type when set.
    asset_filter = models.CharField(max_length=255, blank=True, default="")
    # PRD §17 — meaning depends on condition_type:
    #   score_threshold_cross → score value the rule crosses
    #   convergence           → minimum wallet count (optional, falls back to
    #                           DEFAULT_CONVERGENCE_MIN_WALLETS when NULL)
    #   new_position / position_closed / asset_specific → unused
    threshold = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        default=Channel.BOTH,
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "alertsalertrule"
        indexes = [
            models.Index(fields=["is_active", "condition_type"]),
            models.Index(fields=["wallet", "is_active"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        target = self.wallet.address if self.wallet else "all-targets"
        return f"{self.condition_type} → {target} ({self.user.username})"

    @property
    def asset_set(self) -> set[str]:
        """Parse `asset_filter` into an uppercase asset set; empty = any."""
        if not self.asset_filter:
            return set()
        return {
            part.strip().upper()
            for part in self.asset_filter.split(",")
            if part.strip()
        }


class Notification(BaseModel):
    """One user-facing alert event. UI badge + outbound delivery state."""

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    alert_rule = models.ForeignKey(
        AlertRule,
        on_delete=models.SET_NULL,
        related_name="notifications",
        null=True,
        blank=True,
    )
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
        db_index=True,
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    level = models.CharField(
        max_length=8,
        choices=Level.choices,
        default=Level.INFO,
        db_index=True,
    )
    event_type = models.CharField(max_length=32, db_index=True)
    read = models.BooleanField(default=False, db_index=True)
    # PRD §17 — filled in by the dispatcher (send_pending_notifications).
    # NULL means "queued, not yet delivered"; on success it's stamped with
    # the dispatch timestamp so the task can resume cleanly after a crash.
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "alertsnotification"
        indexes = [
            models.Index(fields=["user", "read", "created_at"]),
            models.Index(fields=["sent_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.level}] {self.title}"


class AlertHistory(BaseModel):
    """
    Append-only record of every AlertRule firing. Drives the dedup check:
    a rule won't fire twice for the same (wallet, event_type, asset) within
    `ALERT_DEDUP_COOLDOWN_SECONDS` (default 1h).
    """

    alert_rule = models.ForeignKey(
        AlertRule,
        on_delete=models.CASCADE,
        related_name="history",
    )
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.SET_NULL,
        related_name="alert_history",
        null=True,
        blank=True,
        db_index=True,
    )
    event_type = models.CharField(max_length=32, db_index=True)
    asset = models.CharField(max_length=20, blank=True, default="", db_index=True)
    notification = models.OneToOneField(
        Notification,
        on_delete=models.SET_NULL,
        related_name="history_entry",
        null=True,
        blank=True,
    )
    fired_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "alertshistory"
        indexes = [
            models.Index(fields=["alert_rule", "wallet", "event_type", "fired_at"]),
            models.Index(fields=["-fired_at"]),
        ]
        ordering = ["-fired_at"]

    def __str__(self) -> str:
        return f"{self.alert_rule} {self.event_type} @ {self.fired_at}"