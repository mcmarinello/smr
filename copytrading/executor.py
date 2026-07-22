"""
Order Executor — places trades on user's HyperLiquid account.

Supports:
- Dry-run mode (logs trades without executing) — default
- Live mode via HyperLiquid SDK Exchange class

CRITICAL SAFETY: Live execution is gated by HL_LIVE_EXECUTION=True env var.
Without this explicit flag, the system always runs in dry-run mode.

Ported from whale-copy/execution/order_executor.py and adapted to
Django/Celery patterns.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from django.conf import settings as django_settings

logger = logging.getLogger(__name__)


class DryRunExecutor:
    """Simulates order execution — logs everything, places nothing."""

    def __init__(self) -> None:
        self.executed_orders: list[dict[str, Any]] = []

    async def open_position(
        self,
        coin: str,
        side: str,
        size_usd: float,
        price: float,
        leverage: int,
        slippage: float = 0.005,
    ) -> dict[str, Any]:
        """Simulate opening a position."""
        if side == "long":
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)

        order: dict[str, Any] = {
            "type": "open",
            "coin": coin,
            "side": side,
            "size_usd": size_usd,
            "size_asset": round(size_usd / fill_price, 6) if fill_price > 0 else 0,
            "price": price,
            "fill_price": round(fill_price, 6),
            "leverage": leverage,
            "slippage": slippage,
            "timestamp": time.time(),
            "status": "simulated",
            "order_id": f"DRY-{int(time.time() * 1000)}",
        }

        self.executed_orders.append(order)
        logger.info(
            "[DRY RUN] OPEN %s %s | $%,.2f (%s) | fill=$%,.4f | lev=%dx",
            side.upper(),
            coin,
            size_usd,
            order["size_asset"],
            fill_price,
            leverage,
        )
        return order

    async def close_position(
        self,
        coin: str,
        side: str,
        size_usd: float,
        price: float,
        leverage: int,
        slippage: float = 0.005,
    ) -> dict[str, Any]:
        """Simulate closing a position."""
        # Opposite slippage for close
        if side == "long":
            fill_price = price * (1 - slippage)  # selling long = slippage down
        else:
            fill_price = price * (1 + slippage)  # covering short = slippage up

        order: dict[str, Any] = {
            "type": "close",
            "coin": coin,
            "side": side,
            "size_usd": size_usd,
            "size_asset": round(size_usd / fill_price, 6) if fill_price > 0 else 0,
            "price": price,
            "fill_price": round(fill_price, 6),
            "leverage": leverage,
            "slippage": slippage,
            "timestamp": time.time(),
            "status": "simulated",
            "order_id": f"DRY-{int(time.time() * 1000)}",
        }

        self.executed_orders.append(order)
        logger.info(
            "[DRY RUN] CLOSE %s %s | $%,.2f | fill=$%,.4f",
            side.upper(),
            coin,
            size_usd,
            fill_price,
        )
        return order

    def get_summary(self) -> dict[str, Any]:
        """Return execution summary."""
        opens = [o for o in self.executed_orders if o["type"] == "open"]
        closes = [o for o in self.executed_orders if o["type"] == "close"]
        return {
            "total_orders": len(self.executed_orders),
            "opens": len(opens),
            "closes": len(closes),
            "total_volume_usd": sum(o["size_usd"] for o in self.executed_orders),
        }


class LiveExecutor:
    """
    Real executor using HyperLiquid SDK.

    REQUIRES:
    - HL_PRIVATE_KEY env var (or passed as private_key)
    - HL_LIVE_EXECUTION=True env var (double safety gate)
    - hyperliquid Python SDK installed

    DANGER: This places REAL orders with REAL money.
    """

    def __init__(
        self,
        private_key: str,
        base_url: Optional[str] = None,
        slippage: float = 0.005,
    ) -> None:
        # Safety check: this should never be instantiated without the flag
        live_enabled = getattr(django_settings, "HL_LIVE_EXECUTION", False)
        if not live_enabled:
            raise RuntimeError(
                "LiveExecutor cannot be instantiated when HL_LIVE_EXECUTION=False. "
                "This is a safety guard — set HL_LIVE_EXECUTION=True explicitly."
            )

        from eth_account import Account
        from hyperliquid.exchange import Exchange

        self.slippage = slippage
        self.wallet = Account.from_key(private_key)

        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url

        self.exchange = Exchange(self.wallet, **kwargs)
        self.executed_orders: list[dict[str, Any]] = []

    async def open_position(
        self,
        coin: str,
        side: str,
        size_usd: float,
        price: float,
        leverage: int,
        slippage: Optional[float] = None,
    ) -> dict[str, Any]:
        """Place a market order to open a position."""
        slp = slippage or self.slippage
        is_buy = side == "long"

        size_asset = round(size_usd / price, 6) if price > 0 else 0
        if size_asset <= 0:
            return {"error": "size_too_small"}

        try:
            # Set leverage
            self.exchange.update_leverage(lev=leverage, coin=coin, is_cross=True)

            # Market open
            result = self.exchange.market_open(
                coin=coin,
                is_buy=is_buy,
                sz=size_asset,
                slippage=slp,
            )

            fill_price = price
            if result and "status" in result:
                if result["status"] == "ok":
                    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                    if statuses and "px" in statuses[0]:
                        fill_price = float(statuses[0]["px"])

            order: dict[str, Any] = {
                "type": "open",
                "coin": coin,
                "side": side,
                "size_usd": size_usd,
                "size_asset": size_asset,
                "price": price,
                "fill_price": fill_price,
                "leverage": leverage,
                "slippage": slp,
                "timestamp": time.time(),
                "status": "filled",
                "result": result,
            }

            self.executed_orders.append(order)
            logger.info(
                "✅ LIVE OPEN %s %s | $%,.2f (%s) | fill=$%,.4f | lev=%dx",
                side.upper(),
                coin,
                size_usd,
                size_asset,
                fill_price,
                leverage,
            )
            return order

        except Exception as e:
            logger.error("❌ Failed to open %s: %s", coin, e)
            return {"error": str(e), "type": "open", "coin": coin}

    async def close_position(
        self,
        coin: str,
        side: str,
        size_usd: float,
        price: float,
        leverage: int,
        slippage: Optional[float] = None,
    ) -> dict[str, Any]:
        """Place a market order to close a position."""
        slp = slippage or self.slippage
        is_buy = side == "short"  # closing short = buying

        size_asset = round(size_usd / price, 6) if price > 0 else 0
        if size_asset <= 0:
            return {"error": "size_too_small"}

        try:
            result = self.exchange.market_close(
                coin=coin,
                sz=size_asset,
                slippage=slp,
            )

            fill_price = price
            if result and "status" in result:
                if result["status"] == "ok":
                    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                    if statuses and "px" in statuses[0]:
                        fill_price = float(statuses[0]["px"])

            order: dict[str, Any] = {
                "type": "close",
                "coin": coin,
                "side": side,
                "size_usd": size_usd,
                "size_asset": size_asset,
                "price": price,
                "fill_price": fill_price,
                "leverage": leverage,
                "slippage": slp,
                "timestamp": time.time(),
                "status": "filled",
                "result": result,
            }

            self.executed_orders.append(order)
            logger.info(
                "✅ LIVE CLOSE %s %s | $%,.2f | fill=$%,.4f",
                side.upper(),
                coin,
                size_usd,
                fill_price,
            )
            return order

        except Exception as e:
            logger.error("❌ Failed to close %s: %s", coin, e)
            return {"error": str(e), "type": "close", "coin": coin}

    def get_summary(self) -> dict[str, Any]:
        opens = [o for o in self.executed_orders if o.get("type") == "open"]
        closes = [o for o in self.executed_orders if o.get("type") == "close"]
        return {
            "total_orders": len(self.executed_orders),
            "opens": len(opens),
            "closes": len(closes),
            "total_volume_usd": sum(o.get("size_usd", 0) for o in self.executed_orders),
        }


def create_executor(config: Optional[dict[str, Any]] = None) -> DryRunExecutor | LiveExecutor:
    """
    Factory: returns DryRunExecutor or LiveExecutor based on config/env.

    Priority:
    1. HL_LIVE_EXECUTION env var (must be "true"/"1"/"yes")
    2. HL_PRIVATE_KEY env var must be set for live mode
    3. Defaults to DryRunExecutor

    This is the ONLY entry point for creating executors — never instantiate
    LiveExecutor directly to ensure the safety gate is always checked.
    """
    cfg = config or {}

    # Check the global kill switch
    live_enabled = cfg.get(
        "live_execution",
        getattr(django_settings, "HL_LIVE_EXECUTION", False),
    )

    if not live_enabled:
        logger.info("⚡ Using DRY RUN executor (no real trades)")
        return DryRunExecutor()

    private_key = cfg.get(
        "private_key",
        os.environ.get("HL_PRIVATE_KEY", ""),
    )
    if not private_key:
        logger.warning("HL_LIVE_EXECUTION=True but no private key found — falling back to DRY RUN")
        return DryRunExecutor()

    base_url = cfg.get("exchange_base_url", None)
    slippage = cfg.get("slippage_tolerance", 0.005)

    logger.warning("🔴 Using LIVE executor — REAL TRADES WILL BE PLACED")
    return LiveExecutor(private_key, base_url=base_url, slippage=slippage)


__all__ = [
    "DryRunExecutor",
    "LiveExecutor",
    "create_executor",
]
