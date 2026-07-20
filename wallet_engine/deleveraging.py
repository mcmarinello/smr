"""
Deleveraging Score Engine — pure functions (PRD §15.4).

Calculates a second score (`score_deleveraged`) over the same raw fills as
`score_raw`, normalizing every position to 1x leverage. The dual scoring
separates directional skill from leverage amplification: the difference
between the two scores yields the Leverage Dependency Index (PRD §15.4).

Design (mirrors score.py / metrics.py):
  - No Django / ORM imports — deterministic and re-runnable from fills alone.
  - Accepts the same fills DataFrame that `wallets.services.fills_to_dataframe`
    produces (mirrors `wallets.Fill` columns).
  - Reuses the nine scoring components from `score.py` so the only thing that
    changes between `score_raw` and `score_deleveraged` is the input PnL
    stream (the components themselves are identical).

How the normalization works:
  1. `deleverage_fills(fills_df)` reconstructs a `price_return_pct` for every
     close fill by FIFO-matching it against prior opens on the same asset and
     opposite side. `deleveraged_pnl` (per fill, sized at 1x with the
     position notional as the capital base) is added as a column. Open fills
     and unmatched closes fall back to `closed_pnl / notional` so the engine
     degrades gracefully when `direction` is missing.
  2. `compute_deleveraged_score(fills_df, window_days, account_value)`
     swaps the `closed_pnl` column for `account_value * price_return_pct`
     (the PRD §15.4 proxy: "capital referência é a `accountValue` mais
     recente") and feeds the resulting DataFrame back into the same
     `compute_metrics_window` + `compute_breakdown` pipeline used by
     `score_raw`. Every PnL-derived metric — total PnL, drawdown, daily
     returns, win/loss counts — therefore reflects only directional skill,
     sized as if the whole account were deployed at 1x for each call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from .metrics import compute_metrics_window
from .score import classify, compute_breakdown


_EPS: float = 1e-12


def _infer_direction_sign(side: str | None) -> float:
    """
    Long positions (opened by buys) profit when exit > entry; shorts
    (opened by sells) profit when exit < entry. The sign applies to the
    price-return computation: a sell-close exits a long, so the return is
    +(exit-entry)/entry; a buy-close exits a short, so the return is
    -(exit-entry)/entry.
    """
    side = (side or "").lower()
    if side == "sell":
        return 1.0
    if side == "buy":
        return -1.0
    return 0.0


def deleverage_fills(fills_df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize `fills_df` to 1x leverage by reconstructing each fill's PnL
    from the underlying price return between entry and exit, rather than
    from the exchange-reported `closed_pnl`.

    Adds two columns:
      - `price_return_pct`: signed fractional price return between the
        matched entry and exit (0 for opens; falls back to
        `closed_pnl / notional` when no open can be paired).
      - `deleveraged_pnl`: notional × `price_return_pct` (the 1x dollar PnL
        using the position notional itself as capital; equivalent to the
        raw `closed_pnl` when leverage was 1x). Downstream consumers should
        rebase to `account_value` (see `compute_deleveraged_score`).

    FIFO matching is performed per asset across opposite-side open/close
    pairs. Long opens (buy + direction=open) are closed by sell closes;
    short opens (sell + direction=open) are closed by buy closes.

    Pure: no Django / ORM imports. Same inputs => same outputs regardless
    of caller (PRD §15 — todo score recalculável a partir do fill bruto).
    """
    if fills_df is None or len(fills_df) == 0:
        out = pd.DataFrame(columns=list(fills_df.columns) if fills_df is not None else [])
        out["price_return_pct"] = pd.Series(dtype=float)
        out["deleveraged_pnl"] = pd.Series(dtype=float)
        return out

    df = fills_df.copy().reset_index(drop=True)

    for col in ("price", "size", "fee", "closed_pnl"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["price_return_pct"] = 0.0
    df["deleveraged_pnl"] = 0.0

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

    if "asset" not in df.columns:
        return df

    for asset, asset_df in df.groupby("asset", sort=False):
        # Per-asset FIFO queues: [entry_price, remaining_size, open_idx]
        opens_long: list[list] = []
        opens_short: list[list] = []

        for idx, row in asset_df.iterrows():
            direction = str(row.get("direction", "") or "").lower()
            side = str(row.get("side", "") or "").lower()
            price = float(row.get("price", 0.0) or 0.0)
            size = float(row.get("size", 0.0) or 0.0)
            closed_pnl = float(row.get("closed_pnl", 0.0) or 0.0)
            notional = abs(price * size)

            if direction == "open":
                target = opens_long if side == "buy" else opens_short if side == "sell" else None
                if target is not None:
                    target.append([price, abs(size), idx])
                continue

            queue: list[list] | None = None
            if direction == "close":
                # A sell close exits a long (bought opens); a buy close exits a short.
                queue = opens_long if side == "sell" else opens_short if side == "buy" else None

            dir_sign = _infer_direction_sign(side) if queue is not None else 0.0

            if queue is None:
                # direction missing or unmatched side — degrade gracefully
                # by deriving the price return directly from closed_pnl.
                if notional > 0 and closed_pnl != 0.0:
                    df.at[idx, "price_return_pct"] = closed_pnl / notional
                    df.at[idx, "deleveraged_pnl"] = closed_pnl
                continue

            remaining = abs(size)
            weighted_return = 0.0
            matched_capital = 0.0
            while remaining > _EPS and queue:
                entry_price, open_remaining, _ = queue[0]
                matched = min(remaining, open_remaining)
                if entry_price > 0:
                    ret = (price - entry_price) / entry_price * dir_sign
                else:
                    ret = 0.0
                cap = entry_price * matched
                weighted_return += ret * cap
                matched_capital += cap
                remaining -= matched
                queue[0][1] -= matched
                if queue[0][1] <= _EPS:
                    queue.pop(0)

            if matched_capital > 0:
                price_return_pct = weighted_return / matched_capital
            elif notional > 0 and closed_pnl != 0.0:
                price_return_pct = closed_pnl / notional
            else:
                price_return_pct = 0.0

            df.at[idx, "price_return_pct"] = price_return_pct
            df.at[idx, "deleveraged_pnl"] = matched_capital * price_return_pct

    return df


def compute_deleveraged_score(
    fills_df: pd.DataFrame,
    window_days: int,
    account_value: float | None = None,
    *,
    now: datetime | None = None,
    market_daily_df: pd.DataFrame | None = None,
) -> tuple[float, dict[str, Any]]:
    """
    Top-level deleveraged-score entrypoint: rebuild the PnL stream at 1x
    leverage using `account_value` as the capital proxy (PRD §15.4 note),
    then run the same nine scoring components used for `score_raw`.

    Returns (score_in_0_100, breakdown_dict) — same shape as
    `wallet_engine.score.compute_score`.
    """
    delveraged = deleverage_fills(fills_df)

    av = float(account_value) if account_value else 0.0
    if av > 0:
        # Re-size every matched close to "what would the dollar PnL be if
        # the entire account had been deployed at 1x for this directional
        # call". Open fills (price_return_pct == 0) contribute 0 PnL here,
        # matching the raw side (opens never realize PnL).
        delveraged["closed_pnl"] = av * delveraged["price_return_pct"]
    else:
        # Fall back to the notional-based reconstruction — keeps the engine
        # usable when account_value is unavailable (mirrors the
        # `_pnl_based_equity_fallback` policy in services.py).
        delveraged["closed_pnl"] = delveraged["deleveraged_pnl"]

    metrics = compute_metrics_window(
        delveraged,
        window_days=window_days,
        account_value=account_value,
        now=now,
        market_daily_df=market_daily_df,
    )
    score, breakdown = compute_breakdown(metrics)
    breakdown["window_days"] = window_days
    breakdown["account_value"] = metrics["account_value"]
    breakdown["total_trades"] = metrics["total_trades"]
    breakdown["classification"] = classify(score)
    breakdown["deleveraged"] = True
    return score, breakdown


def leverage_dependency_index(score_raw: float, score_deleveraged: float) -> float:
    """
    PRD §15.4 — Leverage Dependency Index.

    ```
    leverage_dependency_index = (score_raw − score_deleveraged) / max(score_raw, 1)
    ```

    - Near 0  → edge is directional/skill; leverage is a passive amplifier.
    - High    → result depends heavily on being more leveraged than normal;
                the same trader at 1x would be median at best.
    - Negative is rare but possible: indicates deleveraged skill scores
      better than raw (e.g. a high-leverage trader burning themselves on
      fees / liquidations that disappear when sized at 1x).
    """
    raw = float(score_raw)
    delv = float(score_deleveraged)
    return (raw - delv) / max(raw, 1.0)


__all__ = [
    "deleverage_fills",
    "compute_deleveraged_score",
    "leverage_dependency_index",
]