"""
Parsers that convert raw Hyperliquid API responses into domain dicts
ready to be persisted as wallets.Fill / wallets.Position records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def _ts(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def parse_fill(raw: dict[str, Any], wallet_address: str) -> dict[str, Any]:
    """
    Maps a single fill dict from userFills response to the Fill model field dict.
    Returns None-safe: all optional fields fall back to safe defaults.
    """
    return {
        "asset": raw.get("coin", ""),
        "side": "buy" if raw.get("side") == "B" else "sell",
        "price": Decimal(str(raw.get("px", "0"))),
        "size": Decimal(str(raw.get("sz", "0"))),
        "fee": Decimal(str(raw.get("fee", "0"))),
        "closed_pnl": Decimal(str(raw.get("closedPnl", "0"))),
        "timestamp": _ts(raw["time"]),
        "is_liquidation": raw.get("liquidation") is not None,
        "oid": int(raw.get("oid", 0)),
        "direction": "open" if raw.get("dir", "").startswith("Open") else "close",
        "start_position": (
            Decimal(str(raw["startPosition"])) if raw.get("startPosition") is not None else None
        ),
        "hash": raw.get("hash", ""),
        "tid": raw.get("tid"),
    }


def parse_position(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Maps a single position entry from clearinghouseState['assetPositions']
    to the Position model field dict.
    """
    pos = raw.get("position", raw)
    entry = pos.get("entryPx") or pos.get("entryPrice")
    liq = pos.get("liquidationPx")
    leverage_raw = pos.get("leverage", {})
    leverage_value = leverage_raw.get("value") if isinstance(leverage_raw, dict) else leverage_raw
    return {
        "asset": pos.get("coin", ""),
        "side": "long" if float(pos.get("szi", pos.get("size", 0))) > 0 else "short",
        "size": Decimal(str(abs(float(pos.get("szi", pos.get("size", 0)))))),
        "entry_price": Decimal(str(entry)) if entry else Decimal("0"),
        "leverage": Decimal(str(leverage_value)) if leverage_value else None,
        "liquidation_price": Decimal(str(liq)) if liq else None,
        "unrealized_pnl": (
            Decimal(str(pos.get("unrealizedPnl", "0")))
            if pos.get("unrealizedPnl") is not None
            else None
        ),
        "status": "open",
    }
