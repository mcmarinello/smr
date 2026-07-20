from django.db import models

from wallets.models import BaseModel


class DiscoveryStatus(BaseModel):
    """
    One row per discovery source. Tracks health of the Discovery Engine.
    Used by the dashboard health panel (PRD §18.1) and monitoring.
    """

    class Source(models.TextChoices):
        LEADERBOARD = "leaderboard", "Leaderboard"
        TRADE_STREAM = "trade_stream", "Trade Stream"

    source = models.CharField(max_length=20, choices=Source.choices, unique=True)
    # Cumulative count of wallets ever created by this source
    discovered_count = models.IntegerField(default=0)
    last_scan_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    is_running = models.BooleanField(default=False)

    class Meta:
        db_table = "discovery_status"
        verbose_name = "Discovery Status"
        verbose_name_plural = "Discovery Statuses"

    def __str__(self) -> str:
        return f"{self.source} (count={self.discovered_count}, last={self.last_scan_at})"
