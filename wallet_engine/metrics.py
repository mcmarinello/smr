"""
Pure-Python metrics computation for the Wallet Score Engine.

No Django imports here — callers hand in a pandas DataFrame of fills
(mirrors wallets.Fill columns) and get back a dict of intermediate metrics
that score.py consumes. This keeps the engine re-runnable from raw fills
alone (PRD §15) and unit-testable without a DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

_NUMERIC_COLS: tuple[str, ...] = ("price", "size", "fee", "closed_pnl")


def _empty_metrics(account_value: float | None) -> dict[str, Any]:
    av = float(account_value) if account_value else 0.0
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "neutral": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "total_fees": 0.0,
        "account_value": av,
        "normalized_pnl": 0.0,
        "equity_curve": pd.Series([], dtype=float),
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "current_drawdown": 0.0,
        "current_drawdown_pct": 0.0,
        "daily_pnl": pd.Series([], dtype=float),
        "daily_returns": pd.Series([], dtype=float),
        "daily_returns_std": 0.0,
        "avg_notional": 0.0,
        "notional_std": 0.0,
        "avg_notional_ratio": 0.0,
        "notional_ratio_std": 0.0,
        "max_notional_ratio": 0.0,
        "martingale_severity": 0.0,
        "martingale_events": 0,
        "assets_total_count": 0,
        "assets_positive_count": 0,
        "asset_pnl": {},
        "regime_pnl": {},
        "regimes_positive": 0,
        "regimes_total": 3,
        "window_days": 0,
    }


def _detect_martingale(df: pd.DataFrame) -> tuple[float, int]:
    """
    Heuristic average-down detector.

    Looks at consecutive `open` fills on the same asset/side and counts how
    often each new entry happens at a worse price than the previous one
    (long buying lower, short selling higher). Returns (severity in 0..1,
    event_count). Severity = weighted fraction of aggressive add-ons vs total
    open entries, weighted by position size added.
    """
    if df.empty or "direction" not in df:
        return 0.0, 0

    opens = df[df["direction"] == "open"].copy()
    if opens.empty:
        return 0.0, 0

    severity_sum = 0.0
    weight_sum = 0.0
    events = 0

    for (asset, side), group in opens.groupby(["asset", "side"], sort=False):
        group = group.sort_values("timestamp")
        prev_price = None
        for _, row in group.iterrows():
            price = float(row["price"])
            size = abs(float(row["size"]))
            if prev_price is not None and size > 0:
                adverse = (side == "buy" and price < prev_price) or (
                    side == "sell" and price > prev_price
                )
                if adverse:
                    severity_sum += size
                    events += 1
                weight_sum += size
            prev_price = price

    if weight_sum <= 0:
        return 0.0, 0
    severity = min(severity_sum / weight_sum, 1.0)
    return severity, events


def _regime_breakdown(
    daily_pnl: pd.Series, market_daily_df: pd.DataFrame | None
) -> tuple[dict[str, float], int]:
    """
    Splits the window's days into up/down/sideways regimes using BTC daily
    returns (column `return` on a DatetimeIndex named `day`). Returns a dict
    of regime -> trader PnL, plus the count of regimes with positive PnL.

    If no market data is provided, returns an empty dict and zero so the
    score component can fall back to a neutral value (PRD §15.1 grants the
    engine the right to degrade gracefully while BTC data is being fetched).
    """
    if market_daily_df is None or market_daily_df.empty or daily_pnl.empty:
        return {}, 0

    market = market_daily_df.copy()
    if "return" not in market.columns:
        return {}, 0
    market.index = pd.to_datetime(market.index, utc=True).floor("D")
    market = market[~market.index.duplicated(keep="last")]

    threshold = 0.01  # 1% daily move separates "trending" from "sideways"
    regime = pd.Series("sideways", index=market.index)
    regime[market["return"] > threshold] = "up"
    regime[market["return"] < -threshold] = "down"

    joined = pd.concat([daily_pnl.rename("pnl"), regime.rename("regime")], axis=1)
    joined = joined.dropna(subset=["pnl"])
    pnl_by_regime = joined.groupby("regime")["pnl"].sum().to_dict()

    for key in ("up", "down", "sideways"):
        pnl_by_regime.setdefault(key, 0.0)

    positive = sum(1 for v in pnl_by_regime.values() if v > 0)
    return pnl_by_regime, positive


def compute_metrics_window(
    fills_df: pd.DataFrame,
    window_days: int,
    account_value: float | None = None,
    now: datetime | None = None,
    market_daily_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Filter `fills_df` to the last `window_days` calendar days and return a
    dict with every intermediate metric score.py needs.

    `fills_df` columns expected (mirrors wallets.Fill):
      asset, side, price, size, fee, closed_pnl, timestamp,
      is_liquidation, oid, direction, start_position

    `account_value` is used as the equity reference for normalization
    (PRD §15.1, PnL component note). Falls back to abs(total_pnl)+1 when
    missing so small/zero-balance wallets don't divide by zero.
    `now` defaults to UTC now; pass an explicit value for deterministic tests.
    `market_daily_df` (optional) is BTC daily returns indexed by day.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    if fills_df is None or fills_df.empty:
        return _empty_metrics(account_value)

    df = fills_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

    cutoff = now - timedelta(days=window_days)
    df = df[df["timestamp"] >= cutoff]

    if df.empty:
        return _empty_metrics(account_value)

    for col in _NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    pnl = df["closed_pnl"].astype(float)
    fees = df["fee"].astype(float)
    net_pnl = pnl - fees
    total_pnl = float(net_pnl.sum())
    total_fees = float(fees.sum())

    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    neutral = int((pnl == 0).sum())
    decided = wins + losses
    win_rate = wins / decided if decided else 0.0
    total_trades = int(len(df))

    account_value_f = float(account_value) if account_value else 0.0
    if account_value_f <= 0:
        account_value_f = max(abs(total_pnl), 1.0)
    normalized_pnl = total_pnl / account_value_f

    equity = net_pnl.cumsum()
    peak = equity.cummax()
    drawdown = (peak - equity).clip(lower=0.0)
    max_drawdown = float(drawdown.max()) if drawdown.size else 0.0
    peak_max = float(peak.max()) if peak.size else 0.0
    max_drawdown_pct = max_drawdown / max(peak_max, 1.0)
    current_equity = float(equity.iloc[-1])
    current_peak = float(peak.iloc[-1])
    current_drawdown = max(current_peak - current_equity, 0.0)
    current_drawdown_pct = current_drawdown / max(current_peak, 1.0) if current_peak > 0 else 0.0

    df["day"] = df["timestamp"].dt.floor("D")
    df["net_pnl"] = net_pnl
    daily_pnl = (
        df.groupby("day")["net_pnl"].sum().sort_index()
        if "net_pnl" in df
        else pd.Series(dtype=float)
    )
    full_days = pd.date_range(
        pd.Timestamp(cutoff).floor("D"),
        pd.Timestamp(now).floor("D"),
        freq="D",
    )
    daily_pnl = daily_pnl.reindex(full_days, fill_value=0.0)
    daily_returns = daily_pnl / account_value_f
    daily_returns_std = (
        float(np.std(daily_returns.to_numpy(), ddof=0)) if daily_returns.size else 0.0
    )

    df["notional"] = (df["size"].astype(float) * df["price"].astype(float)).abs()
    notional_ratio = df["notional"] / account_value_f
    avg_notional = float(df["notional"].mean()) if df["notional"].size else 0.0
    notional_std = float(np.std(df["notional"].to_numpy(), ddof=0)) if df["notional"].size else 0.0
    avg_notional_ratio = float(notional_ratio.mean()) if notional_ratio.size else 0.0
    notional_ratio_std = (
        float(np.std(notional_ratio.to_numpy(), ddof=0)) if notional_ratio.size else 0.0
    )
    max_notional_ratio = float(notional_ratio.max()) if notional_ratio.size else 0.0

    martingale_severity, martingale_events = _detect_martingale(df)

    asset_pnl = df.groupby("asset")["net_pnl"].sum().to_dict()
    assets_total_count = len(asset_pnl)
    assets_positive_count = sum(1 for v in asset_pnl.values() if v > 0)

    regime_pnl, regimes_positive = _regime_breakdown(daily_pnl, market_daily_df)

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_fees": total_fees,
        "account_value": account_value_f,
        "normalized_pnl": normalized_pnl,
        "equity_curve": equity,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "current_drawdown": current_drawdown,
        "current_drawdown_pct": current_drawdown_pct,
        "daily_pnl": daily_pnl,
        "daily_returns": daily_returns,
        "daily_returns_std": daily_returns_std,
        "avg_notional": avg_notional,
        "notional_std": notional_std,
        "avg_notional_ratio": avg_notional_ratio,
        "notional_ratio_std": notional_ratio_std,
        "max_notional_ratio": max_notional_ratio,
        "martingale_severity": martingale_severity,
        "martingale_events": martingale_events,
        "assets_total_count": assets_total_count,
        "assets_positive_count": assets_positive_count,
        "asset_pnl": {k: float(v) for k, v in asset_pnl.items()},
        "regime_pnl": {k: float(v) for k, v in regime_pnl.items()},
        "regimes_positive": regimes_positive,
        "regimes_total": 3,
        "window_days": int(window_days),
    }


def window_days_for(window: str) -> int:
    """Map a PRD §15.2 window label to its day count."""
    return {
        "24h": 1,
        "7d": 7,
        "30d": 30,
        "90d": 90,
        "180d": 180,
    }[window]
