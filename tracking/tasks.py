"""
Celery tasks for the Tracking Engine — queue: 'tracking' (routed in
settings.py via CELERY_TASK_ROUTES).

track_wallet         — one-shot: incremental fill fetch + position sync for
                       one wallet. Dispatched by track_all_targets and called
                       directly when a target is freshly promoted.
track_all_targets    — periodic; queues track_wallet for every is_target wallet.
detect_convergence   — periodic; flags assets opened concurrently by 3+ targets.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from wallets.models import Wallet

from .promotion import apply_promotion_demotion
from .services import detect_convergence as _detect_convergence
from .services import track_wallet_fills

logger = logging.getLogger(__name__)

# alerts.tasks is imported lazily inside the wiring points below because
# it in turn imports wallets.models; tracking.tasks is loaded eagerly by
# Celery autodiscovery and pulling alerts at module top could trip an
# import cycle on fresh processes.


@shared_task(
    name="tracking.tasks.track_wallet",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def track_wallet(self, wallet_address: str) -> dict[str, Any]:
    """
    Incremental tracking cycle for a single target wallet (PRD §16.1).
    resilient to transient HL failures via exponential backoff. After a
    successful fill ingestion, fans out one process_wallet_alerts job on
    the 'alerts' queue (Sprint 6 — PRD §17) so new_position /
    position_closed triggers are evaluated independently.
    """
    try:
        summary = track_wallet_fills(wallet_address)
    except Exception as exc:
        logger.exception("track_wallet %s failed: %s", wallet_address, exc)
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)

    if summary.get("position_events"):
        from alerts.tasks import process_wallet_alerts

        process_wallet_alerts.apply_async(
            args=[wallet_address, summary], queue="alerts"
        )
    return summary


@shared_task(name="tracking.tasks.track_all_targets")
def track_all_targets() -> dict[str, Any]:
    """
    Fans out one track_wallet task per is_target wallet. Scheduled every 5
    minutes (CELERY_BEAT_SCHEDULE) so the tracking queue absorbs the load;
    each wallet is processed independently and can be retried in isolation.
    """
    targets = list(
        Wallet.objects.filter(is_target=True, is_active=True)
        .values_list("address", flat=True)
        .order_by("address")
    )
    queued = 0
    for address in targets:
        track_wallet.apply_async(args=[address], queue="tracking")
        queued += 1
    logger.info("track_all_targets: queued %d wallets", queued)
    return {"targets_queued": queued}


@shared_task(name="tracking.tasks.detect_convergence")
def detect_convergence() -> dict[str, Any]:
    """
    PRD §16.2 — scans recent open fills across every target wallet and
    reports (asset, side) clusters opened by 3+ wallets within 2 hours.
    Each cluster fans out to one process_convergence_alert job on the
    'alerts' queue (Sprint 6 — PRD §17) so per-wallet convergence rules
    are evaluated independently of the detection cycle.
    """
    clusters = _detect_convergence()
    if clusters:
        from alerts.tasks import process_convergence_alert

        for cluster in clusters:
            process_convergence_alert.apply_async(
                args=[cluster], queue="alerts"
            )
    logger.info("detect_convergence: %d cluster(s) flagged", len(clusters))
    return {"clusters": clusters, "count": len(clusters)}


@shared_task(
    name="tracking.tasks.apply_promotion_demotion",
    bind=True,
    max_retries=2,
)
def apply_promotion_demotion_task(self, wallet_address: str) -> dict[str, Any]:
    """
    Standalone entrypoint to evaluate promotion/demotion for one wallet after
    its scores have been recomputed. Usually called inline from
    compute_all_scores (piggyback), but exposed here so it can also be queued
    manually (e.g. right after a backfill).
    """
    try:
        wallet = Wallet.objects.get(address=wallet_address.strip().lower())
    except Wallet.DoesNotExist:
        logger.error("apply_promotion_demotion: wallet %s not found", wallet_address)
        return {"address": wallet_address, "applied": False, "reason": "not_found"}
    try:
        return apply_promotion_demotion(wallet)
    except Exception as exc:
        logger.exception(
            "apply_promotion_demotion %s failed: %s", wallet_address, exc
        )
        raise self.retry(exc=exc, countdown=60)