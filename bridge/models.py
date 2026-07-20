"""
TMT Bridge — PRD §20 / Sprint 9.

The bridge app persists only an audit log of every request that hits the
public endpoint. The data contract itself is consumed live from
`wallets.WalletScore` / `wallets.Position`; nothing is cached in the bridge
app because the bridge is born disabled and the contract is documented, not
actively consumed (PRD §20.3).
"""

from __future__ import annotations

from django.db import models


class BridgeAccessLog(models.Model):
    """
    Append-only audit trail of every request to the smart-money-signal
    endpoint. The bridge is a contract, not an open pipe: even when
    disabled (the default) requests are logged so an operator can see who
    tried to reach the endpoint and what the SMR returned.
    """

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    requester_ip = models.GenericIPAddressField(null=True, blank=True)
    endpoint = models.CharField(max_length=255)
    response_code = models.IntegerField()
    data_snapshot = models.JSONField(
        default=dict, blank=True, help_text="JSON body returned to the requester."
    )

    class Meta:
        db_table = "bridge_access_log"
        indexes = [
            models.Index(fields=["-timestamp"]),
            models.Index(fields=["endpoint", "-timestamp"]),
        ]
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"{self.endpoint} {self.response_code} @ {self.timestamp}"