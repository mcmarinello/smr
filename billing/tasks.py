from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from billing.models import CustomerProfile


@shared_task
def expire_subscriptions() -> int:
    return CustomerProfile.objects.filter(
        status=CustomerProfile.Status.ACTIVE,
        current_period_end__lt=timezone.now(),
    ).update(status=CustomerProfile.Status.EXPIRED)
