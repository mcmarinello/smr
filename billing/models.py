from __future__ import annotations

from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import User
from billing.crypto import decrypt_secret, encrypt_secret
from wallets.models import BaseModel, Wallet


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


class Favorite(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorites")
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="favorited_by")

    class Meta:
        unique_together = ("user", "wallet")

    def __str__(self) -> str:
        return f"{self.user.username} ★ {self.wallet.address}"


class PromoCode(BaseModel):
    code = models.CharField(max_length=32, unique=True)
    discount_percent = models.PositiveIntegerField(validators=[MaxValueValidator(100)])
    max_uses = models.PositiveIntegerField(null=True, blank=True)  # None = unlimited
    uses_count = models.PositiveIntegerField(default=0)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.valid_until and timezone.now() > self.valid_until:
            return False
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return False
        return True

    def __str__(self) -> str:
        return f"{self.code} (-{self.discount_percent}%)"
