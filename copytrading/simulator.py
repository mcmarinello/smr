"""
Copy-simulation core — pure functions (PRD §Sprint 8).

No Django/ORM imports: every function takes plain numbers / tuples and
returns plain dicts so the simulator stays deterministic and re-runnable
from the raw fills alone (CLAUDE.md: "todo score recalculável a partir do
fill bruto").

Strategy → size multiplier (PRD §Sprint 8):
    conservative : 0.5
    moderate     : 1.0
    aggressive   : 1.5

Per-trade notional is derived from the target's `allocation_pct` of the
profile's virtual capital, capped by the profile's `max_position_pct`,
then scaled by the strategy multiplier.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

STRATEGY_SIZE_MULTIPLIER: dict[str, Decimal] = {
    "conservative": Decimal("0.5"),
    "moderate": Decimal("1.0"),
    "aggressive": Decimal("1.5"),
}

# Precision guard: virtual trades are denominated in USD so quantize to
# the cent — eliminates float noise from the pnl arithmetic below.
_USD = Decimal("0.01")


def _quantize_usd(value: Decimal) -> Decimal:
    return value.quantize(_USD, rounding=ROUND_DOWN)


def simulate_open(
    *,
    strategy: str,
    max_position_pct: Decimal,
    allocation_pct: Decimal,
    fill_side: str,
    fill_price: Decimal,
    current_capital: Decimal,
    fill_direction: str = "open",
) -> dict[str, Any] | None:
    """
    Returns the SimulatedTrade field bag for an open triggered by `fill`.

    The position size in USD is:

        target_capital = current_capital * allocation_pct / 100
        per_trade_cap = target_capital * max_position_pct / 100
        size_usd     = per_trade_cap * strategy_multiplier

    `fill_direction` is "open" (new trade) — passing "close" returns None so
    the caller can short-circuit without having to special-case it before
    invoking the function.

    `fill_side` follows Hyperliquid's fill semantics ("buy"/"sell") and is
    mapped to the long/short trade side here so the caller can store the
    field bag directly.
    """
    if fill_direction != "open":
        return None
    if fill_price is None or fill_price <= 0:
        return None
    if current_capital is None or current_capital <= 0:
        return None

    multiplier = STRATEGY_SIZE_MULTIPLIER.get(strategy, Decimal("1"))
    target_capital = current_capital * (allocation_pct / Decimal("100"))
    per_trade_cap = target_capital * (max_position_pct / Decimal("100"))
    size_usd = _quantize_usd(per_trade_cap * multiplier)
    if size_usd <= 0:
        return None

    side = "long" if fill_side == "buy" else "short"
    return {
        "side": side,
        "entry_price": fill_price,
        "size_usd": size_usd,
    }


def simulate_close(
    *,
    side: str,
    entry_price: Decimal,
    size_usd: Decimal,
    exit_price: Decimal,
) -> dict[str, Any]:
    """
    Realized PnL for a closing fill:

        units = size_usd / entry_price
        long  → pnl = units * (exit_price - entry_price)
        short → pnl = units * (entry_price - exit_price)

    Returns {exit_price, pnl_usd} quantized to USD cents.
    """
    if entry_price is None or entry_price <= 0 or exit_price is None:
        return {"exit_price": exit_price, "pnl_usd": None}

    units = size_usd / entry_price
    if side == "long":
        pnl = units * (exit_price - entry_price)
    else:
        pnl = units * (entry_price - exit_price)
    return {"exit_price": exit_price, "pnl_usd": _quantize_usd(pnl)}


def compute_virtual_equity(
    *,
    initial_capital: Decimal,
    closed_pnl: Decimal,
    open_trades: list[dict[str, Any]],
) -> Decimal:
    """
    Current virtual equity = initial_capital + realized PnL (sum of closed
    trades) + unrealized PnL (each open trade marked at its current price).

    `open_trades` is a list of dicts with keys:
        side, entry_price, size_usd, current_price
    All inputs/outputs are Decimal; result is quantized to USD cents.
    """
    equity = initial_capital + (closed_pnl or Decimal("0"))
    for t in open_trades:
        cur = t.get("current_price")
        if cur is None:
            continue
        unreal = simulate_close(
            side=t["side"],
            entry_price=t["entry_price"],
            size_usd=t["size_usd"],
            exit_price=cur,
        )["pnl_usd"]
        if unreal is not None:
            equity += unreal
    return _quantize_usd(equity)


__all__ = [
    "STRATEGY_SIZE_MULTIPLIER",
    "simulate_open",
    "simulate_close",
    "compute_virtual_equity",
]