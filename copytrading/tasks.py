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

# ── Whale Copy — Live Execution Tasks (PRD §19) ──────────


@shared_task(
    name="copytrading.tasks.execute_whale_signal",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def execute_whale_signal(self, change_data: dict[str, Any]) -> dict[str, Any]:
    """
    Process a PositionChange from the UserFillsSubscriber and optionally
    execute a trade (dry-run or live based on HL_LIVE_EXECUTION flag).

    change_data is a serialized PositionChange dict containing:
    whale_address, coin, action, side, entry_price, new_size, leverage, etc.

    Safety gates:
    1. HL_LIVE_EXECUTION must be True for real orders
    2. RiskManager checks exposure/position limits
    3. create_executor() handles the dry/live split
    """
    import asyncio
    from copytrading.executor import create_executor
    from copytrading.risk_manager import RiskConfig, RiskManager
    from django.conf import settings as smr_settings

    coin = change_data.get("coin", "")
    action = change_data.get("action", "")
    side = change_data.get("side", "")
    price = change_data.get("entry_price", 0)
    size_new = change_data.get("new_size", 0)
    leverage = change_data.get("leverage", 1)
    whale_addr = change_data.get("whale_address", "")

    logger.info(
        "execute_whale_signal: %s %s %s @ $%.4f (whale=%s)",
        action, coin, side, price, whale_addr[:10] if whale_addr else "?",
    )

    # Only act on open/close actions, ignore size_change, etc.
    is_open = action.startswith("open_") or action.startswith("size_increase_")
    is_close = action.startswith("close_") or action.startswith("size_decrease_")

    if not is_open and not is_close:
        return {"action": action, "status": "skipped", "reason": "not_open_or_close"}

    # Build risk config from Django settings
    risk_config = RiskConfig(
        capital_per_trade_usd=smr_settings.HL_CAPITAL_PER_TRADE_USD,
        max_leverage=smr_settings.HL_MAX_LEVERAGE,
        max_exposure_pct=smr_settings.HL_MAX_EXPOSURE_PCT,
        max_open_positions=smr_settings.HL_MAX_OPEN_POSITIONS,
        stop_loss_pct=smr_settings.HL_STOP_LOSS_PCT,
        take_profit_pct=smr_settings.HL_TAKE_PROFIT_PCT,
        min_score_to_copy=smr_settings.HL_MIN_SCORE_TO_COPY,
        slippage_tolerance=smr_settings.HL_SLIPPAGE_TOLERANCE,
    )

    risk_mgr = RiskManager(risk_config)

    # Use a mock account_value for now — in production, fetch from HL
    account_value = 1000.0  # TODO: fetch actual account value

    if is_open:
        # Check if we can open
        can_open, reason = risk_mgr.can_open(account_value)
        if not can_open:
            logger.info("execute_whale_signal: cannot open — %s", reason)
            return {"action": action, "status": "blocked", "reason": reason}

        # Calculate position size
        size_usd = risk_mgr.calculate_size(account_value, size_new * price, leverage)
        if size_usd <= 0:
            return {"action": action, "status": "skipped", "reason": "zero_size"}

        # Create executor (dry-run or live)
        executor = create_executor()

        # Execute async
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                executor.open_position(
                    coin=coin, side=side, size_usd=size_usd,
                    price=price, leverage=leverage,
                )
            )
        finally:
            loop.close()

        # Register position in risk manager
        pos = risk_mgr.open_position(
            whale_address=whale_addr, coin=coin, side=side,
            size_usd=size_usd, entry_price=price, leverage=leverage,
        )

        return {
            "action": action,
            "status": result.get("status", "unknown"),
            "coin": coin,
            "side": side,
            "size_usd": size_usd,
            "position_id": pos.position_id,
        }

    else:  # is_close
        executor = create_executor()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                executor.close_position(
                    coin=coin, side=side, size_usd=0,
                    price=price, leverage=leverage,
                )
            )
        finally:
            loop.close()

        return {
            "action": action,
            "status": result.get("status", "unknown"),
            "coin": coin,
            "side": side,
        }


@shared_task(name="copytrading.tasks.process_whale_fills_batch")
def process_whale_fills_batch(changes_data: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Batch process multiple PositionChange events.
    Called by the UserFillsSubscriber callback or the polling beat.
    """
    results = []
    for change in changes_data:
        try:
            result = execute_whale_signal.delay(change)
            results.append({"task_id": result.id, "status": "queued"})
        except Exception as e:
            logger.error("Failed to queue whale signal: %s", e)
            results.append({"status": "error", "error": str(e)})

    return {"processed": len(results), "results": results}
