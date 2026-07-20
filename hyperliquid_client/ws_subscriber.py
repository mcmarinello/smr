"""
WebSocket subscriber for the Hyperliquid public trades stream.

Usage (asyncio):
    async with TradesSubscriber(coins=["BTC", "ETH"]) as sub:
        async for event in sub:
            handle(event)

Each event is the raw dict from HL: {"coin": ..., "side": ..., "px": ..., "sz": ...,
"users": ["0xmaker", "0xtaker"], "time": ..., "hash": ...}
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from django.conf import settings

logger = logging.getLogger(__name__)


class TradesSubscriber:
    """
    Subscribes to the `trades` channel for the given list of coins.
    Yields individual trade dicts. Reconnects automatically on disconnect.
    """

    def __init__(
        self,
        coins: list[str],
        ws_url: str | None = None,
        reconnect_delay: float = 3.0,
    ) -> None:
        self._coins = coins
        self._ws_url = ws_url or getattr(
            settings, "HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws"
        )
        self._reconnect_delay = reconnect_delay
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._running = False
        self._ws_task: asyncio.Task | None = None

    async def _subscribe_all(self, ws: Any) -> None:
        for coin in self._coins:
            msg = json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}})
            await ws.send(msg)

    async def _listen(self) -> None:
        while self._running:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    await self._subscribe_all(ws)
                    logger.info("WS connected, subscribed to %d coins", len(self._coins))
                    async for raw in ws:
                        data = json.loads(raw)
                        if not isinstance(data, dict):
                            continue
                        channel = data.get("channel")
                        if channel == "trades":
                            for trade in data.get("data", []):
                                await self._queue.put(trade)
            except (websockets.ConnectionClosed, OSError) as exc:
                if self._running:
                    logger.warning("WS disconnected (%s), reconnecting in %ss", exc, self._reconnect_delay)
                    await asyncio.sleep(self._reconnect_delay)

    async def __aenter__(self) -> "TradesSubscriber":
        self._running = True
        self._ws_task = asyncio.create_task(self._listen())
        return self

    async def __aexit__(self, *_: Any) -> None:
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self

    async def __anext__(self) -> dict[str, Any]:
        return await self._queue.get()
