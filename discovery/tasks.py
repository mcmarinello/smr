"""
Celery tasks for the Discovery Engine — queue: 'discovery' (routed in settings.py).

fetch_leaderboard      — periodic (every 6h via beat); seeds wallet universe from HL leaderboard
consume_trade_stream   — periodic or manual; extracts addresses from WS trades stream
backfill_wallet        — one-shot per wallet; fetches fills + positions, delegates to wallets/services
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from wallets.services import fetch_and_persist_wallet

from .services import (
    run_leaderboard_scan,
    run_trade_stream_scan,
)

logger = logging.getLogger(__name__)

_STREAM_DURATION_SECS: int = getattr(settings, "DISCOVERY_STREAM_DURATION_SECS", 300)


# ---------------------------------------------------------------------------
# fetch_leaderboard
# ---------------------------------------------------------------------------


@shared_task(
    name="discovery.tasks.fetch_leaderboard",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def fetch_leaderboard(self) -> dict[str, Any]:
    """
    Fetches the Hyperliquid leaderboard, creates new Wallet records,
    and queues backfill tasks for each newly discovered address.
    Runs on queue 'discovery' (see CELERY_TASK_ROUTES in settings.py).
    Scheduled every 6h via CELERY_BEAT_SCHEDULE.
    """
    try:
        result = run_leaderboard_scan()
        for address in result["new_addresses"]:
            backfill_wallet.apply_async(args=[address], queue="discovery")
        logger.info(
            "fetch_leaderboard: %d seen, %d new, %d backfills queued",
            result["total"], result["new"], len(result["new_addresses"]),
        )
        return {k: v for k, v in result.items() if k != "new_addresses"}
    except Exception as exc:
        logger.exception("fetch_leaderboard failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# consume_trade_stream
# ---------------------------------------------------------------------------


@shared_task(
    name="discovery.tasks.consume_trade_stream",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    # Time limits accommodate the full stream window + DB processing overhead
    soft_time_limit=_STREAM_DURATION_SECS + 60,
    time_limit=_STREAM_DURATION_SECS + 120,
)
def consume_trade_stream(self, duration_secs: int | None = None) -> dict[str, Any]:
    """
    Subscribes to the Hyperliquid WebSocket trades stream for top perps.
    Extracts wallet addresses from the 'users' field of each trade event
    and creates new Wallet records (source=trade_stream).
    Queues backfill_wallet for each newly discovered address.

    duration_secs: how long to listen (default: DISCOVERY_STREAM_DURATION_SECS).
    """
    effective_duration = duration_secs or _STREAM_DURATION_SECS
    try:
        result = run_trade_stream_scan(effective_duration)
        for address in result["new_addresses"]:
            backfill_wallet.apply_async(args=[address], queue="discovery")
        logger.info(
            "consume_trade_stream: %d unique addresses, %d new, %d backfills queued",
            result["total"], result["new"], len(result["new_addresses"]),
        )
        return {k: v for k, v in result.items() if k != "new_addresses"}
    except Exception as exc:
        logger.exception("consume_trade_stream failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# backfill_wallet
# ---------------------------------------------------------------------------


@shared_task(
    name="discovery.tasks.backfill_wallet",
    bind=True,
    max_retries=5,
)
def backfill_wallet(self, address: str) -> dict[str, Any]:
    """
    Fetches userFills + clearinghouseState for `address` and persists both.
    Called automatically for newly discovered wallets (by fetch_leaderboard
    and consume_trade_stream) and available for manual invocation.
    Uses exponential backoff on retry (30s → 60s → 120s → 240s → 480s).
    """
    try:
        result = fetch_and_persist_wallet(address)
        logger.info(
            "backfill_wallet %s: %d new fills, %d open positions",
            address, result["new_fills_persisted"], result["open_positions"],
        )
        return result
    except Exception as exc:
        logger.exception("backfill_wallet %s failed: %s", address, exc)
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
