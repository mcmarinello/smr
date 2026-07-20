"""
Service layer for the Discovery Engine.
Contains the core logic for leaderboard scanning and trade-stream scanning.
Both Celery tasks and the 'discover' management command call these functions.
No Celery imports here — callers decide how to queue backfills.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from django.conf import settings
from django.db.models import F

from hyperliquid_client.client import HyperliquidClient
from hyperliquid_client.ws_subscriber import TradesSubscriber
from wallets.models import Wallet

from .models import DiscoveryStatus

logger = logging.getLogger(__name__)

_FALLBACK_COINS = [
    "BTC", "ETH", "SOL", "ARB", "DOGE", "AVAX", "LINK", "BNB", "SUI", "OP",
    "INJ", "TIA", "SEI", "APT", "MATIC", "LTC", "ATOM", "NEAR", "FTM", "CRV",
]


def discover_address(address: str, source: str) -> tuple[Wallet, bool]:
    """
    Get-or-create a Wallet by address.
    Returns (wallet, is_new). Increments discovered_count for the source on creation.
    """
    address = address.strip().lower()
    wallet, created = Wallet.objects.get_or_create(
        address=address,
        defaults={"discovery_source": source},
    )
    if created:
        DiscoveryStatus.objects.filter(source=source).update(
            discovered_count=F("discovered_count") + 1,
        )
        logger.debug("New wallet discovered via %s: %s", source, address)
    return wallet, created


def mark_scan_started(source: str) -> None:
    DiscoveryStatus.objects.update_or_create(
        source=source,
        defaults={"is_running": True, "last_error": ""},
    )


def mark_scan_done(source: str) -> None:
    DiscoveryStatus.objects.filter(source=source).update(
        is_running=False,
        last_scan_at=datetime.now(tz=timezone.utc),
        last_error="",
    )


def mark_scan_error(source: str, error: str) -> None:
    DiscoveryStatus.objects.filter(source=source).update(
        is_running=False,
        last_scan_at=datetime.now(tz=timezone.utc),
        last_error=str(error)[:1000],
    )


def get_status_summary() -> dict[str, Any]:
    """Returns a health-panel summary of all discovery statuses."""
    return {
        row.source: {
            "discovered_count": row.discovered_count,
            "last_scan_at": row.last_scan_at.isoformat() if row.last_scan_at else None,
            "is_running": row.is_running,
            "last_error": row.last_error,
        }
        for row in DiscoveryStatus.objects.all()
    }


# ---------------------------------------------------------------------------
# Core scan logic (called by tasks and management command)
# ---------------------------------------------------------------------------


def run_leaderboard_scan() -> dict[str, Any]:
    """
    Fetches the Hyperliquid leaderboard and discovers new wallet addresses.

    Returns a dict with:
      - total: number of valid addresses seen in the leaderboard
      - new: number of wallets newly created
      - new_addresses: list of newly discovered addresses (for backfill)
    """
    source = Wallet.DiscoverySource.LEADERBOARD
    mark_scan_started(source)
    new_addresses: list[str] = []
    total_count = 0

    try:
        with HyperliquidClient() as client:
            data = client.leaderboard()

        rows = data.get("leaderboardRows", [])
        if not rows:
            logger.warning("Leaderboard returned no rows")

        for row in rows:
            address = (row.get("ethAddress") or row.get("address") or "").strip().lower()
            if not _is_valid_address(address):
                continue
            total_count += 1
            _, created = discover_address(address, source)
            if created:
                new_addresses.append(address)

        mark_scan_done(source)
        logger.info(
            "Leaderboard scan done: %d seen, %d new", total_count, len(new_addresses)
        )
        return {"total": total_count, "new": len(new_addresses), "new_addresses": new_addresses}

    except Exception as exc:
        mark_scan_error(source, str(exc))
        raise


def run_trade_stream_scan(duration_secs: int) -> dict[str, Any]:
    """
    Listens to the Hyperliquid WebSocket trades stream for `duration_secs` seconds.
    Extracts wallet addresses from the 'users' field and discovers new wallets.

    Returns a dict with:
      - total: unique addresses seen
      - new: number of wallets newly created
      - new_addresses: list of newly discovered addresses (for backfill)
      - coins_subscribed: how many coins were subscribed
    """
    source = Wallet.DiscoverySource.TRADE_STREAM
    mark_scan_started(source)

    try:
        coins = _get_top_coins()
        logger.info(
            "Trade stream: subscribing to %d coins for %ds", len(coins), duration_secs
        )

        seen_addresses = asyncio.run(_collect_stream_addresses(coins, duration_secs))

        new_addresses: list[str] = []
        for address in seen_addresses:
            _, created = discover_address(address, source)
            if created:
                new_addresses.append(address)

        mark_scan_done(source)
        logger.info(
            "Trade stream scan done (%ds): %d unique addresses, %d new wallets",
            duration_secs, len(seen_addresses), len(new_addresses),
        )
        return {
            "total": len(seen_addresses),
            "new": len(new_addresses),
            "new_addresses": new_addresses,
            "coins_subscribed": len(coins),
        }

    except Exception as exc:
        mark_scan_error(source, str(exc))
        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_valid_address(address: str) -> bool:
    return bool(address) and address.startswith("0x") and len(address) == 42


def _get_top_coins(n: int | None = None) -> list[str]:
    """Returns coin names from the HL meta endpoint (top N perps)."""
    max_coins: int = n or getattr(settings, "DISCOVERY_MAX_STREAM_COINS", 20)
    try:
        with HyperliquidClient() as client:
            meta = client.meta()
        universe = meta.get("universe", [])
        coins = [asset["name"] for asset in universe[:max_coins] if asset.get("name")]
        if coins:
            return coins
    except Exception as exc:
        logger.warning("Failed to fetch meta for coin list: %s — using fallback", exc)
    return _FALLBACK_COINS[:max_coins]


async def _collect_stream_addresses(coins: list[str], duration_secs: int) -> set[str]:
    """Async: listen to the trade stream and collect unique wallet addresses."""
    seen: set[str] = set()
    async with TradesSubscriber(coins=coins) as sub:
        try:
            async with asyncio.timeout(duration_secs):
                async for trade in sub:
                    for address in trade.get("users", []):
                        address = address.strip().lower()
                        if _is_valid_address(address):
                            seen.add(address)
        except asyncio.TimeoutError:
            pass  # expected: stream ran for the configured duration
    return seen
