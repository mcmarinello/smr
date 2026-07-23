from __future__ import annotations

from django.db import models

from accounts.models import User
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
