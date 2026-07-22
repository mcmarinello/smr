"""
Risk Manager — position sizing, exposure limits, stop-loss, take-profit.

Ported from whale-copy/risk/risk_manager.py and adapted to
Django-compatible patterns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RiskConfig:
    """Risk parameters for copy trading."""

    capital_per_trade_usd: float = 50.0
    max_leverage: int = 5
    max_exposure_pct: float = 25.0
    max_open_positions: int = 5
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 15.0
    min_score_to_copy: int = 55
    slippage_tolerance: float = 0.005

    @classmethod
    def from_dict(cls, d: dict) -> "RiskConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class CopyPosition:
    """A position being copied from a whale."""

    position_id: str
    whale_address: str
    coin: str
    side: str  # "long" or "short"
    size_usd: float
    entry_price: float
    leverage: int
    opened_at: float
    slippage: float = 0.0
    close_price: Optional[float] = None
    closed_at: Optional[float] = None
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    close_reason: str = ""
    status: str = "open"

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "whale_address": self.whale_address,
            "coin": self.coin,
            "side": self.side,
            "size_usd": self.size_usd,
            "entry_price": self.entry_price,
            "leverage": self.leverage,
            "opened_at": self.opened_at,
            "close_price": self.close_price,
            "closed_at": self.closed_at,
            "pnl_usd": self.pnl_usd,
            "pnl_pct": self.pnl_pct,
            "close_reason": self.close_reason,
            "status": self.status,
        }


class RiskManager:
    """Centralized risk controls for copy trading."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.open_positions: list[CopyPosition] = []
        self._position_counter = 0

    def can_open(self, account_value: float) -> tuple[bool, str]:
        """Check whether a new position can be opened."""
        if len(self.open_positions) >= self.config.max_open_positions:
            return False, "max_positions_reached"

        current_exposure = sum(p.size_usd for p in self.open_positions)
        max_exposure = account_value * (self.config.max_exposure_pct / 100)
        if current_exposure >= max_exposure:
            return False, "max_exposure_reached"

        return True, "ok"

    def calculate_size(
        self, account_value: float, whale_size_usd: float, leverage: int
    ) -> float:
        """
        Position size in USD.
        Strategy: proportional to whale's size, capped by risk limits.
        """
        # Cap leverage
        lev = min(leverage, self.config.max_leverage)

        # Proportional to whale (max 5% of whale size to avoid over-exposure)
        proportional = whale_size_usd * 0.05

        # Absolute cap
        abs_cap = self.config.capital_per_trade_usd * lev

        # Exposure cap
        current_exposure = sum(p.size_usd for p in self.open_positions)
        max_total = account_value * (self.config.max_exposure_pct / 100)
        exposure_headroom = max_total - current_exposure

        size = min(proportional, abs_cap, exposure_headroom)
        return max(size, 0)

    def open_position(
        self,
        whale_address: str,
        coin: str,
        side: str,
        size_usd: float,
        entry_price: float,
        leverage: int,
    ) -> CopyPosition:
        """Register a new copied position."""
        self._position_counter += 1
        pid = f"CP-{self._position_counter:06d}"
        pos = CopyPosition(
            position_id=pid,
            whale_address=whale_address,
            coin=coin,
            side=side,
            size_usd=size_usd,
            entry_price=entry_price,
            leverage=min(leverage, self.config.max_leverage),
            opened_at=time.time(),
        )
        self.open_positions.append(pos)
        return pos

    def close_position(
        self, position_id: str, close_price: float, reason: str = "whale_closed"
    ) -> Optional[CopyPosition]:
        """Close a position and calculate PnL."""
        for pos in self.open_positions:
            if pos.position_id == position_id:
                pos.close_price = close_price
                pos.closed_at = time.time()
                pos.close_reason = reason
                pos.status = "closed"

                if pos.side == "long":
                    pnl_pct = (close_price - pos.entry_price) / pos.entry_price * 100
                else:
                    pnl_pct = (pos.entry_price - close_price) / pos.entry_price * 100

                pos.pnl_pct = round(pnl_pct, 4)
                pos.pnl_usd = round(pos.size_usd * (pnl_pct / 100), 2)

                self.open_positions = [
                    p for p in self.open_positions if p.position_id != position_id
                ]
                return pos
        return None

    def check_stop_loss(self, position_id: str, current_price: float) -> bool:
        """Returns True if stop loss is triggered."""
        for pos in self.open_positions:
            if pos.position_id == position_id:
                if pos.side == "long":
                    pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                else:
                    pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100

                if pnl_pct <= -self.config.stop_loss_pct:
                    return True
        return False

    def check_take_profit(self, position_id: str, current_price: float) -> bool:
        """Returns True if take profit is triggered."""
        for pos in self.open_positions:
            if pos.position_id == position_id:
                if pos.side == "long":
                    pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                else:
                    pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100

                if pnl_pct >= self.config.take_profit_pct:
                    return True
        return False

    def total_exposure(self) -> float:
        return sum(p.size_usd for p in self.open_positions)

    def unrealized_pnl(self) -> float:
        return sum(p.pnl_usd for p in self.open_positions)


__all__ = [
    "RiskConfig",
    "CopyPosition",
    "RiskManager",
]
