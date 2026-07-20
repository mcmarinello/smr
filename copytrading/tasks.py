"""
Celery tasks for the Copy Trading simulator — queue: 'copytrading'
(routed in settings.py via CELERY_TASK_ROUTES).

run_copy_simulation        — per-profile: iterate its active targets and
                             replay their recent fills through the simulator.
run_all_copy_simulations    — periodic; fans run_copy_simulation out, one
                             per active profile. Scheduled every 15m.
auto_close_stale            — per-profile: flatten open trades older than
                             the profile's max_hold_hours. Useful both as
                             a beat entry and as a one-off cleanup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from celery import shared_task

from .models import CopyTradingProfile, SimulatedTrade
from .services import process_copy_signals

logger = logging.getLogger(__name__)


@shared_task(
    name="copytrading.tasks.run_copy_simulation",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def run_copy_simulation(self, profile_id: int) -> dict[str, Any]:
    """
    Iterate every active CopyTradingTarget owned by `profile_id` and replay
    that target wallet's recent fills through the simulator. Independent of
    the tracking beat so a profile with many targets can be retried in
    isolation when a single wallet spikes the rate limiter.
    """
    try:
        profile = CopyTradingProfile.objects.get(pk=profile_id, is_active=True)
    except CopyTradingProfile.DoesNotExist:
        logger.info("run_copy_simulation: profile %s inactive/missing", profile_id)
        return {"profile_id": profile_id, "opens": 0, "closes": 0}

    opens = 0
    closes = 0
    targets_touched = 0
    for address in profile.targets.filter(is_active=True).values_list(
        "wallet__address", flat=True
    ):
        try:
            summary = process_copy_signals(address)
        except Exception as exc:
            logger.exception(
                "run_copy_simulation profile=%s wallet=%s failed: %s",
                profile_id,
                address,
                exc,
            )
            continue
        opens += summary.get("opens", 0)
        closes += summary.get("closes", 0)
        if summary.get("profiles_touched"):
            targets_touched += 1
    logger.info(
        "run_copy_simulation profile=%s: %d targets, %d opens, %d closes",
        profile_id,
        targets_touched,
        opens,
        closes,
    )
    return {
        "profile_id": profile_id,
        "targets_touched": targets_touched,
        "opens": opens,
        "closes": closes,
    }


@shared_task(name="copytrading.tasks.run_all_copy_simulations")
def run_all_copy_simulations() -> dict[str, Any]:
    """
    Beat entrypoint — schedules one run_copy_simulation per active profile.
    Triggered every 15 minutes by CELERY_BEAT_SCHEDULE.
    """
    profile_ids = list(
        CopyTradingProfile.objects.filter(is_active=True).values_list("id", flat=True)
    )
    queued = 0
    for pid in profile_ids:
        run_copy_simulation.apply_async(args=[pid], queue="copytrading")
        queued += 1
    logger.info("run_all_copy_simulations: queued %d profiles", queued)
    return {"profiles_queued": queued}


@shared_task(
    name="copytrading.tasks.auto_close_stale",
    bind=True,
    max_retries=2,
)
def auto_close_stale(self, profile_id: int) -> dict[str, Any]:
    """
    Flatten open SimulatedTrades older than `profile.max_hold_hours`. Uses
    the trade's entry_price as the exit (V1 paper: no live mid) so the
    realized PnL reflects time-stop only — the dashboard can still surface
    these as time-stopped closes. `max_hold_hours=0` disables the policy
    for the profile.
    """
    try:
        profile = CopyTradingProfile.objects.get(pk=profile_id)
    except CopyTradingProfile.DoesNotExist:
        logger.info("auto_close_stale: profile %s missing", profile_id)
        return {"profile_id": profile_id, "closed": 0}

    if not profile.max_hold_hours:
        return {"profile_id": profile_id, "closed": 0}

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=profile.max_hold_hours)
    stale_qs = SimulatedTrade.objects.filter(
        profile=profile,
        status=SimulatedTrade.Status.OPEN.value,
        opened_at__lt=cutoff,
    )
    closed = 0
    for trade in stale_qs:
        # Time-stop close: mark at entry so realized PnL is zero unless the
        # simulator computes a non-zero delta from rounding.
        trade.exit_price = trade.entry_price
        trade.pnl_usd = Decimal("0")
        trade.status = SimulatedTrade.Status.CLOSED.value
        trade.closed_at = datetime.now(tz=timezone.utc)
        trade.save(
            update_fields=[
                "exit_price",
                "pnl_usd",
                "status",
                "closed_at",
                "updated_at",
            ]
        )
        closed += 1
    if closed:
        logger.info(
            "auto_close_stale profile=%s: closed %d stale trades",
            profile_id,
            closed,
        )
    return {"profile_id": profile_id, "closed": closed}


@shared_task(name="copytrading.tasks.auto_close_stale_all")
def auto_close_stale_all() -> dict[str, Any]:
    """Beat helper: schedules auto_close_stale for every active profile."""
    profile_ids = list(
        CopyTradingProfile.objects.filter(is_active=True).values_list("id", flat=True)
    )
    queued = 0
    for pid in profile_ids:
        auto_close_stale.apply_async(args=[pid], queue="copytrading")
        queued += 1
    return {"profiles_queued": queued}