from __future__ import annotations

from django.db import models

from accounts.models import User
from billing.crypto import decrypt_secret, encrypt_secret
from wallets.models import BaseModel


class CustomerProfile(BaseModel):
    class Status(models.TextChoices):
        FREE = "free", "Free"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"

    class Interval(models.TextChoices):
        MONTHLY = "monthly", "Mensal"
        ANNUAL = "annual", "Anual"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FREE)
    plan_interval = models.CharField(max_length=20, choices=Interval.choices, null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    email_verified = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.user.username} ({self.status})"


class ExchangeCredential(BaseModel):
    """
    Schema-only placeholder (multi-tenant foundation spec). No connection or
    order-execution logic reads this yet — that is the multi-broker copy
    trading spec's job.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="exchange_credentials")
    exchange = models.CharField(max_length=20)  # "binance", "bybit", ...
    api_key_encrypted = models.TextField()
    api_secret_encrypted = models.TextField()
    is_active = models.BooleanField(default=True)

    def set_api_key(self, plain: str) -> None:
        self.api_key_encrypted = encrypt_secret(plain)

    def set_api_secret(self, plain: str) -> None:
        self.api_secret_encrypted = encrypt_secret(plain)

    def get_api_key(self) -> str:
        return decrypt_secret(self.api_key_encrypted)

    def get_api_secret(self) -> str:
        return decrypt_secret(self.api_secret_encrypted)

    def __str__(self) -> str:
        return f"{self.user.username} — {self.exchange}"
