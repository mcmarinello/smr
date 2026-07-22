"""
UserFills WebSocket Subscriber — detects whale position changes via
real-time fill events + periodic REST snapshot diff.

Ported from whale-copy/monitors/whale_detector.py and adapted to
Django/celery-compatible patterns.

Usage (asyncio):
    async with UserFillsSubscriber(whales=[...]) as sub:
        async for change in sub:
            handle(change)

Or via Celery:
    from copytrading.tasks import process_whale_fills
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

import aiohttp
import websockets
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)


# ── Data Models ──────────────────────────────────────────


@dataclass
class WhaleSnapshot:
    """Point-in-time snapshot of a whale's positions."""

    address: str
    positions: dict  # {coin: {"side": str, "size": float, "entry": float, ...}}
    account_value: float = 0.0
    timestamp: float = 0.0


@dataclass
class PositionChange:
    """Detected change in a whale's position."""

    whale_address: str
    coin: str
    action: str  # "open_long", "open_short", "close_long", "close_short", "size_change"
    old_size: float
    new_size: float
    entry_price: float
    leverage: int
    side: str
    timestamp: float
    fill_data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "whale_address": self.whale_address,
            "coin": self.coin,
            "action": self.action,
            "old_size": self.old_size,
            "new_size": self.new_size,
            "entry_price": self.entry_price,
            "leverage": self.leverage,
            "side": self.side,
            "timestamp": self.timestamp,
        }


# ── Callback type ──
ChangeCallback = Callable[[PositionChange], Awaitable[None]]


# ── REST Snapshot Fetcher ────────────────────────────────


async def fetch_whale_positions(
    http: aiohttp.ClientSession, address: str
) -> Optional[WhaleSnapshot]:
    """Fetch current positions from clearinghouse state."""
    try:
        url = getattr(django_settings, "HYPERLIQUID_INFO_API_URL", "https://api.hyperliquid.xyz/info")
        payload = {"type": "clearinghouseState", "user": address}
        async with http.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception as e:
        logger.warning("Failed to fetch positions for %s: %s", address[:12], e)
        return None

    positions = {}
    for ap in data.get("assetPositions", []):
        pos = ap.get("position", {})
        coin = pos.get("coin", "")
        size = float(pos.get("sizeDecimal", "0"))
        if abs(size) > 0:
            positions[coin] = {
                "side": "long" if size > 0 else "short",
                "size": abs(size),
                "entry": float(pos.get("entryPx", "0")),
                "mark": float(pos.get("markPx", "0")),
                "pnl": float(pos.get("unrealizedPnl", "0")),
                "leverage": int(float(pos.get("leverage", {}).get("value", "1"))),
                "raw_size": size,
            }

    account_value = float(data.get("marginSummary", {}).get("accountValue", 0))

    return WhaleSnapshot(
        address=address,
        positions=positions,
        account_value=account_value,
        timestamp=time.time(),
    )


# ── Snapshot Diff Engine ─────────────────────────────────


def diff_snapshots(old: WhaleSnapshot, new: WhaleSnapshot) -> List[PositionChange]:
    """Compare two snapshots and return position changes."""
    changes = []
    now = new.timestamp

    all_coins = set(old.positions.keys()) | set(new.positions.keys())

    for coin in all_coins:
        old_p = old.positions.get(coin)
        new_p = new.positions.get(coin)

        if old_p is None and new_p is not None:
            # New position opened
            changes.append(
                PositionChange(
                    whale_address=new.address,
                    coin=coin,
                    action=f"open_{new_p['side']}",
                    old_size=0,
                    new_size=new_p["size"],
                    entry_price=new_p["entry"],
                    leverage=new_p["leverage"],
                    side=new_p["side"],
                    timestamp=now,
                )
            )
        elif old_p is not None and new_p is None:
            # Position closed
            changes.append(
                PositionChange(
                    whale_address=new.address,
                    coin=coin,
                    action=f"close_{old_p['side']}",
                    old_size=old_p["size"],
                    new_size=0,
                    entry_price=old_p["entry"],
                    leverage=old_p["leverage"],
                    side=old_p["side"],
                    timestamp=now,
                )
            )
        elif old_p is not None and new_p is not None:
            # Same coin — check size or side change
            if abs(old_p["size"] - new_p["size"]) > 0.001:
                if new_p["size"] > old_p["size"]:
                    action = f"size_increase_{new_p['side']}"
                else:
                    action = f"size_decrease_{new_p['side']}"

                # Side flip
                if old_p["side"] != new_p["side"]:
                    action = f"flip_to_{new_p['side']}"

                changes.append(
                    PositionChange(
                        whale_address=new.address,
                        coin=coin,
                        action=action,
                        old_size=old_p["size"],
                        new_size=new_p["size"],
                        entry_price=new_p["entry"],
                        leverage=new_p["leverage"],
                        side=new_p["side"],
                        timestamp=now,
                    )
                )

    return changes


# ── Fill-based Detector (real-time) ──────────────────────


def parse_fill_direction(fill: dict) -> Optional[str]:
    """
    Parse HyperLiquid fill direction into our action format.
    fill["dir"] examples: "Open Long", "Close Short", "Open Short", "Close Long"
    """
    direction = fill.get("dir", "")

    dir_map = {
        "Open Long": "open_long",
        "Open Short": "open_short",
        "Close Long": "close_long",
        "Close Short": "close_short",
    }
    return dir_map.get(direction)


# ── Main Subscriber Class ─────────────────────────────────


class UserFillsSubscriber:
    """
    Monitors whale positions via:
    - Periodic REST snapshots (catches everything)
    - WebSocket user fills (real-time, more precise)

    Fires callbacks on position changes.

    Compatible with Django async views and Celery async tasks.
    """

    def __init__(
        self,
        reconnect_delay: float = 5.0,
        poll_interval: float = 10.0,
    ) -> None:
        self.snapshots: Dict[str, WhaleSnapshot] = {}
        self.callbacks: List[ChangeCallback] = []
        self._running = False
        self._ws: Optional[Any] = None
        self._watched: Set[str] = set()
        self._seen_fills: Set[str] = set()
        self._http: Optional[aiohttp.ClientSession] = None
        self._reconnect_delay = reconnect_delay
        self._poll_interval = poll_interval

    def on_change(self, callback: ChangeCallback) -> None:
        """Register callback for position changes."""
        self.callbacks.append(callback)

    async def _fire_callbacks(self, change: PositionChange) -> None:
        for cb in self.callbacks:
            try:
                await cb(change)
            except Exception as e:
                logger.error("Callback error: %s", e)

    # ── REST Polling ───────────────────────────────────

    async def snapshot_cycle(self, addresses: List[str], http: aiohttp.ClientSession) -> None:
        """One cycle: fetch all whale snapshots and diff."""
        for addr in addresses:
            new_snap = await fetch_whale_positions(http, addr)
            if new_snap is None:
                continue

            old_snap = self.snapshots.get(addr)
            if old_snap is not None:
                changes = diff_snapshots(old_snap, new_snap)
                for change in changes:
                    logger.info(
                        "🔄 %s.. | %s | %s | %s → %s",
                        change.whale_address[:10],
                        change.coin,
                        change.action,
                        change.old_size,
                        change.new_size,
                    )
                    await self._fire_callbacks(change)

            self.snapshots[addr] = new_snap

    async def poll_loop(self, addresses: List[str]) -> None:
        """Continuous polling loop."""
        self._http = aiohttp.ClientSession()
        self._running = True
        try:
            while self._running:
                await self.snapshot_cycle(addresses, self._http)
                await asyncio.sleep(self._poll_interval)
        finally:
            if self._http:
                await self._http.close()

    # ── WebSocket Real-time ────────────────────────────

    async def _ws_connect(self) -> None:
        """Connect to HyperLiquid WebSocket."""
        ws_url = getattr(django_settings, "HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws")
        self._ws = await websockets.connect(ws_url)
        logger.info("WebSocket connected")

    async def _ws_subscribe_user_fills(self, addresses: List[str]) -> None:
        """Subscribe to userFills for each whale."""
        for addr in addresses:
            msg = {
                "method": "subscribe",
                "subscription": {
                    "type": "userFills",
                    "user": addr,
                },
            }
            await self._ws.send(json.dumps(msg))
            self._watched.add(addr)
            logger.info("Subscribed to fills: %s...", addr[:12])
            await asyncio.sleep(0.15)  # rate limit

    async def _ws_handle_fill(self, data: dict) -> None:
        """Process a user fill event and fire callbacks."""
        fills = data if isinstance(data, list) else [data]
        for fill in fills:
            action = parse_fill_direction(fill)
            if action is None:
                continue  # partial fill or unknown

            # Dedup: key = coin + time + direction
            dedup_key = f"{fill.get('coin')}_{fill.get('time')}_{action}"
            if dedup_key in self._seen_fills:
                continue
            self._seen_fills.add(dedup_key)

            # Prune dedup set (keep last 10k)
            if len(self._seen_fills) > 10_000:
                self._seen_fills = set(list(self._seen_fills)[-5_000:])

            coin = fill.get("coin", "")
            size = abs(float(fill.get("sz", "0")))
            price = float(fill.get("px", "0"))

            # Find whale address from the user field
            whale_addr = fill.get(
                "user",
                fill.get("users", [None])[0]
                if isinstance(fill.get("users"), list) and fill.get("users")
                else None,
            )

            if not whale_addr:
                continue

            # Look up leverage from snapshot
            snap = self.snapshots.get(whale_addr)
            leverage = 1
            side = "long" if "long" in action else "short"
            if snap and coin in snap.positions:
                leverage = snap.positions[coin].get("leverage", 1)
                side = snap.positions[coin].get("side", side)

            change = PositionChange(
                whale_address=whale_addr,
                coin=coin,
                action=action,
                old_size=0,
                new_size=size,
                entry_price=price,
                leverage=leverage,
                side=side,
                timestamp=time.time() / 1000 if fill.get("time", 0) > 1e12 else time.time(),
                fill_data=fill,
            )

            logger.info(
                "⚡ WS Fill | %s.. | %s | %s | sz=%s @ $%,.4f",
                whale_addr[:10],
                coin,
                action,
                size,
                price,
            )
            await self._fire_callbacks(change)

    async def ws_loop(self, addresses: List[str]) -> None:
        """WebSocket listener with auto-reconnect."""
        self._running = True
        while self._running:
            try:
                await self._ws_connect()
                await self._ws_subscribe_user_fills(addresses)

                async for message in self._ws:
                    data = json.loads(message)
                    if not isinstance(data, dict):
                        logger.debug("Non-dict message: %s", type(data))
                        continue
                    channel = data.get("channel", "")

                    if channel == "userFills":
                        events = data.get("data", [])
                        if isinstance(events, list):
                            for event in events:
                                if isinstance(event, dict):
                                    await self._ws_handle_fill(event)
                        elif isinstance(events, dict):
                            await self._ws_handle_fill(events)
                    elif channel == "pong":
                        pass  # heartbeat
                    elif channel == "error":
                        logger.error("WS error: %s", data)
                    elif channel == "subscribeResponse":
                        logger.debug("Subscribed: %s", data.get("data", ""))

            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.InvalidStatusCode,
                ConnectionError,
                OSError,
            ) as e:
                logger.warning("WS disconnected: %s, reconnecting in %ss...", e, self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
            except Exception as e:
                logger.error("WS unexpected error: %s", e)
                await asyncio.sleep(self._reconnect_delay)

    def stop(self) -> None:
        """Stop all loops."""
        self._running = False

    # ── Django async context manager ───────────────────

    async def __aenter__(self) -> "UserFillsSubscriber":
        self._running = True
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.stop()
        if self._http:
            await self._http.close()
        if self._ws:
            await self._ws.close()


__all__ = [
    "WhaleSnapshot",
    "PositionChange",
    "UserFillsSubscriber",
    "fetch_whale_positions",
    "diff_snapshots",
    "parse_fill_direction",
]
