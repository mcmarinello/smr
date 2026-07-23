from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from billing.models import CryptoPayment, CustomerProfile


@shared_task
def expire_subscriptions() -> int:
    return CustomerProfile.objects.filter(
        status=CustomerProfile.Status.ACTIVE,
        current_period_end__lt=timezone.now(),
    ).update(status=CustomerProfile.Status.EXPIRED)


@shared_task
def expire_crypto_payments() -> int:
    return CryptoPayment.objects.filter(
        status=CryptoPayment.Status.PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=CryptoPayment.Status.EXPIRED)
