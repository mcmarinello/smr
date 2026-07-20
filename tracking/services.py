"""
Service layer for the Tracking Engine (PRD §16).

track_wallet_fills   — incremental fill fetch + position sync for one wallet.
detect_convergence   — find assets recently opened by 3+ target wallets.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q

from hyperliquid_client.client import HyperliquidClient
from hyperliquid_client.parsers import parse_fill
from wallets.models import Fill, Position, Wallet

logger = logging.getLogger(__name__)

# Treat position sizes below this absolute threshold as flat (Decimal arith
# noise after partial closes). Sizes are in base asset units (e.g. BTC).
_FLAT_EPSILON = Decimal("1e-8")


def track_wallet_fills(wallet_address: str) -> dict[str, Any]:
    """
    PRD §16.1 — incremental fill ingestion for one wallet.

    1. userFills(since=last_seen_fill_timestamp)
    2. Persist new Fills (dedup by oid)
    3. Open/increase/reduce/close/liquidate Positions according to each fill

    Returns a summary dict {new_fills, updated_positions, closed_positions}.
    """
    address = wallet_address.strip().lower()
    try:
        wallet = Wallet.objects.get(address=address)
    except Wallet.DoesNotExist:
        logger.warning("track_wallet_fills: wallet %s not found", address)
        return {"address": address, "new_fills": 0, "updated_positions": 0, "closed_positions": 0}

    raw_fills = _fetch_incremental_fills(wallet)
    if not raw_fills:
        logger.debug("track_wallet_fills %s: no new fills", address)
        return {"address": address, "new_fills": 0, "updated_positions": 0, "closed_positions": 0}

    new_fills = _persist_new_fills(wallet, raw_fills)
    if not new_fills:
        return {"address": address, "new_fills": 0, "updated_positions": 0, "closed_positions": 0}

    # Apply fills chronologically so position transitions stay consistent.
    new_fills.sort(key=lambda f: f.timestamp)
    updated_positions, closed_positions = _apply_fills_to_positions(wallet, new_fills)

    # Advance the incremental cursor to the newest fill we just persisted.
    latest_ts = max(f.timestamp for f in new_fills)
    Wallet.objects.filter(pk=wallet.pk).update(
        last_seen_fill_timestamp=latest_ts,
        last_seen=datetime.now(tz=timezone.utc),
    )

    logger.info(
        "track_wallet_fills %s: %d new fills, %d updated, %d closed",
        address,
        len(new_fills),
        updated_positions,
        closed_positions,
    )
    return {
        "address": address,
        "new_fills": len(new_fills),
        "updated_positions": updated_positions,
        "closed_positions": closed_positions,
    }


def _fetch_incremental_fills(wallet: Wallet) -> list[dict[str, Any]]:
    """
    Uses userFillsByTime when a cursor exists, falling back to userFills
    (last ~2000) on first tracking cycle. Honors HL rate limiter inside client.
    """
    cursor = wallet.last_seen_fill_timestamp
    try:
        with HyperliquidClient() as client:
            if cursor is not None:
                start_ms = int(cursor.timestamp() * 1000) + 1
                return client.user_fills_by_time(wallet.address, start_ms)
            return client.user_fills(wallet.address)
    except Exception as exc:
        logger.warning("HL userFills fetch failed for %s: %s", wallet.address, exc)
        raise


def _persist_new_fills(wallet: Wallet, raw_fills: list[dict[str, Any]]) -> list[Fill]:
    """Dedup by oid and bulk-create. Returns the Fill instances actually persisted."""
    existing_oids = set(
        Fill.objects.filter(wallet=wallet).values_list("oid", flat=True)
    )
    new_instances: list[Fill] = []
    for raw in raw_fills:
        try:
            parsed = parse_fill(raw, wallet.address)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "Skipping malformed fill for %s: %s — %r", wallet.address, exc, raw
            )
            continue
        if parsed["oid"] in existing_oids:
            continue
        new_instances.append(Fill(wallet=wallet, **parsed))
        existing_oids.add(parsed["oid"])

    if new_instances:
        Fill.objects.bulk_create(new_instances, ignore_conflicts=True)
    return new_instances


def _apply_fills_to_positions(
    wallet: Wallet, fills: list[Fill]
) -> tuple[int, int]:
    """
    Replays each new fill against the wallet's positions. A single Position row
    per (wallet, asset, side) is the source of truth — open is created lazily,
    increases/reduces mutate size, and full flatten (or liquidation) marks it
    closed with `closed_at`. Returns (updated_count, closed_count).
    """
    updated_assets: set[tuple[str, str]] = set()
    closed_count = 0

    for fill in fills:
        side = _fill_position_side(fill)
        if side is None:
            continue

        with transaction.atomic():
            if fill.is_liquidation:
                closed_count += _handle_liquidation(wallet, fill, side, updated_assets)
                continue

            if fill.direction == Fill.Direction.OPEN.value:
                _handle_open(wallet, fill, side, updated_assets)
            else:
                closed_count += _handle_close(wallet, fill, side, updated_assets)

    return len(updated_assets), closed_count


def _fill_position_side(fill: Fill) -> str | None:
    """
    Maps a fill to the position side (long/short) it belongs to.

    Open buy  / close sell → long
    Open sell / close buy  → short
    """
    if fill.direction not in (Fill.Direction.OPEN.value, Fill.Direction.CLOSE.value):
        return None
    if fill.direction == Fill.Direction.OPEN.value:
        return Position.Side.LONG.value if fill.side == Fill.Side.BUY.value else Position.Side.SHORT.value
    # Close: opposite side closes the position.
    return Position.Side.LONG.value if fill.side == Fill.Side.SELL.value else Position.Side.SHORT.value


def _handle_open(
    wallet: Wallet,
    fill: Fill,
    side: str,
    touched: set[tuple[str, str]],
) -> None:
    pos = Position.objects.filter(
        wallet=wallet, asset=fill.asset, side=side, status=Position.Status.OPEN.value
    ).first()
    fill_size = fill.size if fill.side == Fill.Side.BUY.value else fill.size
    if pos is None:
        # Flip from the opposite side if one exists (rare but possible when
        # the wallet reverses without an explicit flat close).
        opposite = Position.objects.filter(
            wallet=wallet,
            asset=fill.asset,
            side=_opposite(side),
            status=Position.Status.OPEN.value,
        ).first()
        if opposite is not None:
            remaining = opposite.size - fill_size
            touched.add((opposite.asset, opposite.side))
            if remaining <= _FLAT_EPSILON:
                opposite.status = Position.Status.CLOSED.value
                opposite.closed_at = fill.timestamp
                opposite.size = Decimal("0")
                opposite.save(update_fields=["status", "closed_at", "size", "updated_at"])
            else:
                opposite.size = remaining
                opposite.save(update_fields=["size", "updated_at"])
        pos = Position.objects.create(
            wallet=wallet,
            asset=fill.asset,
            side=side,
            size=fill_size,
            entry_price=fill.price,
            opened_at=fill.timestamp,
            status=Position.Status.OPEN.value,
        )
    else:
        # Increase: weighted average entry price.
        new_size = pos.size + fill_size
        pos.entry_price = (
            (pos.entry_price * pos.size + fill.price * fill_size) / new_size
            if new_size > 0
            else fill.price
        )
        pos.size = new_size
        pos.save(update_fields=["size", "entry_price", "updated_at"])
    touched.add((pos.asset, pos.side))


def _handle_close(
    wallet: Wallet,
    fill: Fill,
    side: str,
    touched: set[tuple[str, str]],
) -> int:
    pos = Position.objects.filter(
        wallet=wallet, asset=fill.asset, side=side, status=Position.Status.OPEN.value
    ).first()
    if pos is None:
        # Close arrived without a tracked open (e.g. opened before tracking
        # began). Nothing to mutate; do not crank counter.
        touched.add((fill.asset, side))
        return 0

    remaining = pos.size - fill.size
    touched.add((pos.asset, pos.side))
    if remaining <= _FLAT_EPSILON:
        pos.status = Position.Status.CLOSED.value
        pos.closed_at = fill.timestamp
        pos.size = Decimal("0")
        pos.save(update_fields=["status", "closed_at", "size", "updated_at"])
        return 1
    pos.size = remaining
    pos.save(update_fields=["size", "updated_at"])
    return 0


def _handle_liquidation(
    wallet: Wallet,
    fill: Fill,
    side: str,
    touched: set[tuple[str, str]],
) -> int:
    """Liquidation forces the position closed regardless of remaining size."""
    pos = Position.objects.filter(
        wallet=wallet, asset=fill.asset, side=side, status=Position.Status.OPEN.value
    ).first()
    if pos is None:
        touched.add((fill.asset, side))
        return 0
    pos.status = Position.Status.CLOSED.value
    pos.closed_at = fill.timestamp
    pos.size = Decimal("0")
    pos.save(update_fields=["status", "closed_at", "size", "updated_at"])
    touched.add((pos.asset, pos.side))
    return 1


def _opposite(side: str) -> str:
    return (
        Position.Side.SHORT.value
        if side == Position.Side.LONG.value
        else Position.Side.LONG.value
    )


# ---------------------------------------------------------------------------
# Convergence detection (PRD §16.2)
# ---------------------------------------------------------------------------


DEFAULT_CONVERGENCE_WINDOW_MINUTES = 120
DEFAULT_CONVERGENCE_MIN_WALLETS = 3


def detect_convergence(
    *,
    window_minutes: int = DEFAULT_CONVERGENCE_WINDOW_MINUTES,
    min_wallets: int = DEFAULT_CONVERGENCE_MIN_WALLETS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Finds assets where 3+ target wallets opened a position on the same side
    within the last `window_minutes`. Returns one entry per (asset, side)
    cluster with the participating wallet addresses and fill timestamps, so
    callers (alerts task) can dispatch notifications.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    since = now - timedelta(minutes=window_minutes)

    opens = (
        Fill.objects.filter(
            wallet__is_target=True,
            direction=Fill.Direction.OPEN.value,
            timestamp__gte=since,
        )
        .select_related("wallet")
        .only("wallet__address", "asset", "side", "timestamp")
        .order_by("timestamp")
    )

    clusters: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"wallets": set(), "fills": []}
    )
    for fill in opens:
        side = (
            Position.Side.LONG.value
            if fill.side == Fill.Side.BUY.value
            else Position.Side.SHORT.value
        )
        key = (fill.asset, side)
        clusters[key]["wallets"].add(fill.wallet.address)
        clusters[key]["fills"].append(
            {
                "wallet": fill.wallet.address,
                "timestamp": fill.timestamp.isoformat(),
            }
        )

    results: list[dict[str, Any]] = []
    for (asset, side), cluster in clusters.items():
        if len(cluster["wallets"]) >= min_wallets:
            results.append(
                {
                    "asset": asset,
                    "side": side,
                    "wallets": sorted(cluster["wallets"]),
                    "wallet_count": len(cluster["wallets"]),
                    "first_seen": cluster["fills"][0]["timestamp"],
                    "last_seen": cluster["fills"][-1]["timestamp"],
                    "fills": cluster["fills"],
                }
            )
    results.sort(key=lambda r: r["wallet_count"], reverse=True)
    return results