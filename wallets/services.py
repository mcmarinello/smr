"""
Service layer for persisting wallet data.
Called by management commands, Celery tasks, and discovery/tracking workers.
All DB writes go through here — views and tasks should not use the ORM directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from django.db import transaction

from hyperliquid_client.client import HyperliquidClient
from hyperliquid_client.parsers import parse_fill, parse_position
from .models import Wallet, Fill, Position

logger = logging.getLogger(__name__)


def get_or_create_wallet(address: str, source: str = "manual") -> tuple[Wallet, bool]:
    return Wallet.objects.get_or_create(
        address=address,
        defaults={"discovery_source": source},
    )


def persist_fills(wallet: Wallet, raw_fills: list[dict[str, Any]]) -> int:
    """
    Upserts fills for a wallet. Returns number of new fills created.
    Dedup key: oid (Hyperliquid order ID).
    """
    if not raw_fills:
        return 0

    existing_oids = set(
        Fill.objects.filter(wallet=wallet).values_list("oid", flat=True)
    )
    new_fills = []
    for raw in raw_fills:
        try:
            parsed = parse_fill(raw, wallet.address)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping malformed fill for %s: %s — %r", wallet.address, exc, raw)
            continue
        if parsed["oid"] in existing_oids:
            continue
        new_fills.append(Fill(wallet=wallet, **parsed))

    if new_fills:
        Fill.objects.bulk_create(new_fills, ignore_conflicts=True)
        logger.info("Persisted %d new fills for %s", len(new_fills), wallet.address)

    wallet.last_seen = datetime.now(tz=timezone.utc)
    wallet.save(update_fields=["last_seen", "updated_at"])
    return len(new_fills)


def sync_positions(wallet: Wallet, clearinghouse: dict[str, Any]) -> None:
    """
    Replaces open positions for a wallet with the snapshot from clearinghouseState.
    Marks positions no longer in the snapshot as closed.
    """
    raw_positions = clearinghouse.get("assetPositions", [])

    with transaction.atomic():
        Position.objects.filter(wallet=wallet, status="open").update(status="closed")

        for raw in raw_positions:
            try:
                parsed = parse_position(raw)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Skipping malformed position for %s: %s", wallet.address, exc)
                continue
            if float(parsed["size"]) == 0:
                continue
            Position.objects.update_or_create(
                wallet=wallet,
                asset=parsed["asset"],
                status="open",
                defaults={**parsed, "opened_at": datetime.now(tz=timezone.utc)},
            )


def fetch_and_persist_wallet(address: str, source: str = "manual") -> dict[str, Any]:
    """
    Fetches fills + clearinghouseState for an address and persists both.
    Returns a summary dict. Used by the fetch_wallet management command and
    by Celery backfill tasks (discovery.tasks.backfill_wallet).

    `source` is only used when creating a new Wallet record; if the wallet
    already exists its discovery_source is not changed.
    """
    wallet, created = get_or_create_wallet(address, source=source)
    logger.info("%s wallet %s", "Created" if created else "Found", address)

    with HyperliquidClient() as client:
        raw_fills = client.user_fills(address)
        clearinghouse = client.clearinghouse_state(address)

    new_fill_count = persist_fills(wallet, raw_fills)
    sync_positions(wallet, clearinghouse)

    open_position_count = Position.objects.filter(wallet=wallet, status="open").count()
    return {
        "address": address,
        "created": created,
        "total_fills_returned": len(raw_fills),
        "new_fills_persisted": new_fill_count,
        "open_positions": open_position_count,
    }
