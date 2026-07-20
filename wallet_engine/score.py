"""
Wallet Score Engine — pure functions (PRD §15).

`compute_score` takes a DataFrame of raw fills plus window metadata and
returns a (score, breakdown) tuple. No Django / ORM imports — everything
here is deterministic and re-runnable from fills alone.

Component weights match PRD §15.1 and sum to 100:

    sampling        10
    win_rate         15
    pnl              20
    drawdown         15
    consistency      15
    risk_per_trade   10
    martingale        5
    diversification   5
    regime            5
    -------------------
    total           100

Classification bands (PRD §15.3):
    0-39   fraco
    40-59  mediano
    60-74  bom
    75-100 elite
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .metrics import compute_metrics_window


# Maximum points each component can contribute to the base score.
WEIGHTS: dict[str, float] = {
    "sampling": 10.0,
    "win_rate": 15.0,
    "pnl": 20.0,
    "drawdown": 15.0,
    "consistency": 15.0,
    "risk_per_trade": 10.0,
    "martingale": 5.0,
    "diversification": 5.0,
    "regime": 5.0,
}

CLASSIFICATION_BANDS: tuple[tuple[float, str], ...] = (
    (75.0, "elite"),
    (60.0, "bom"),
    (40.0, "mediano"),
    (0.0, "fraco"),
)


def classify(score: float) -> str:
    """Map a 0-100 numeric score to a PRD §15.3 classification label."""
    s = max(0.0, min(100.0, float(score)))
    for threshold, label in CLASSIFICATION_BANDS:
        if s >= threshold:
            return label
    return "fraco"


# ---------------------------------------------------------------------------
# Component helpers — each returns (points_awarded, detail_dict)
# ---------------------------------------------------------------------------


def _sampling_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    `min(10, log(total_trades) * 2.5)` — diminishing confidence return.
    A single trade yields 0 points so a lucky 3-trade wallet cannot compete
    with a 300-trade one on sample size alone (PRD §15.1).
    """
    n = max(metrics["total_trades"], 1)
    points = min(WEIGHTS["sampling"], math.log(n) * 2.5)
    points = max(0.0, points)
    return points, {
        "total_trades": metrics["total_trades"],
        "raw": math.log(n) * 2.5,
        "cap": WEIGHTS["sampling"],
    }


def _win_rate_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    `win_rate * 15` reduced by a sample-size factor so small-N win rates
    carry less weight. Combined with the sampling component, this ensures a
    wallet with 3 winning trades does not score near-perfect here.
    """
    win_rate = metrics["win_rate"]
    n = metrics["wins"] + metrics["losses"]
    sample_factor = min(1.0, n / 30.0) if n > 0 else 0.0
    points = win_rate * WEIGHTS["win_rate"] * sample_factor
    return points, {
        "win_rate": win_rate,
        "wins": metrics["wins"],
        "losses": metrics["losses"],
        "sample_factor": sample_factor,
    }


def _pnl_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    PnL absolute normalized by account_value, scaled 0-20 via a logistic
    curve (PRD §15.1 post-validation note: ROI% removed in favor of
    normalized absolute PnL using the most recent accountValue as proxy).
    """
    x = metrics["normalized_pnl"]
    k = 8.0  # logistic steepness — +/- 50% return saturates the curve
    sigmoid = 1.0 / (1.0 + math.exp(-k * x))
    points = sigmoid * WEIGHTS["pnl"]
    return points, {
        "total_pnl": metrics["total_pnl"],
        "account_value": metrics["account_value"],
        "normalized_pnl": x,
        "sigmoid": sigmoid,
    }


def _drawdown_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Penalize max drawdown of the window and how close current equity is to
    that historical max (PRD §15.1). 0% drawdown => full 15 points.
    """
    max_dd_pct = min(max(metrics["max_drawdown_pct"], 0.0), 1.0)
    base = WEIGHTS["drawdown"] * (1.0 - max_dd_pct)
    # current proximity penalty — if currently in deepest DD, halve remaining.
    if metrics["max_drawdown_pct"] > 0:
        proximity = min(metrics["current_drawdown_pct"] / metrics["max_drawdown_pct"], 1.0)
    else:
        proximity = 0.0
    points = base * (1.0 - 0.5 * proximity)
    return points, {
        "max_drawdown": metrics["max_drawdown"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "current_drawdown_pct": metrics["current_drawdown_pct"],
        "proximity": proximity,
    }


def _consistency_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Standard deviation of daily returns — lower is better. Exponential decay:
    zero volatility => 15 pts; higher std drops fast. Decays with k tuned so
    ~1%/day std already halves the score and ~5%/day nearly zeroes it.
    """
    std = metrics["daily_returns_std"]
    k = 60.0
    points = WEIGHTS["consistency"] * math.exp(-k * std)
    return points, {
        "daily_returns_std": std,
        "k": k,
    }


def _risk_per_trade_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Average position sizing vs equity, plus stability across trades. Target
    ratio of ~0.5 (i.e. positions sized at ~half equity, sane leverage). Big
    bets (max_ratio > ~2) and erratic sizing are both punished.
    """
    avg_ratio = metrics["avg_notional_ratio"]
    max_ratio = metrics["max_notional_ratio"]
    sizing_factor = math.exp(-((avg_ratio - 0.5) ** 2) / 0.5)
    overexposure_factor = math.exp(-max(max_ratio - 2.0, 0.0) * 0.5)
    stability_factor = math.exp(-metrics["notional_ratio_std"] * 2.0)
    points = WEIGHTS["risk_per_trade"] * sizing_factor * overexposure_factor * stability_factor
    return points, {
        "avg_notional_ratio": avg_ratio,
        "max_notional_ratio": max_ratio,
        "notional_ratio_std": metrics["notional_ratio_std"],
        "sizing_factor": sizing_factor,
        "overexposure_factor": overexposure_factor,
        "stability_factor": stability_factor,
    }


def _martingale_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Penalize repeated average-down (adding to losing side at worse prices)
    even when current PnL is positive — sinal de má gestão de risco.
    Severity in 0..1 maps to 5*(1-severity) points.
    """
    severity = metrics["martingale_severity"]
    points = WEIGHTS["martingale"] * (1.0 - severity)
    return points, {
        "severity": severity,
        "events": metrics["martingale_events"],
    }


def _diversification_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Reward multiple assets with positive PnL. 3+ distinct assets with gains
    saturate the 5-pt bucket; 1 asset only scores ~1.67 (1/3 of weight).
    """
    positive = metrics["assets_positive_count"]
    threshold = 3
    ratio = min(positive / threshold, 1.0)
    points = WEIGHTS["diversification"] * ratio
    return points, {
        "assets_positive": positive,
        "assets_total": metrics["assets_total_count"],
        "threshold": threshold,
    }


def _regime_component(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compare performance across BTC up / down / sideways regimes (PRD §15.1).
    When market data is unavailable, the component returns its neutral mid
    value (2.5/5) so the total never artificially penalizes wallets whose
    market context was never loaded.
    """
    total_regimes = metrics["regimes_total"]
    positive_regimes = metrics["regimes_positive"]
    if not metrics["regime_pnl"]:
        return WEIGHTS["regime"] / 2.0, {
            "regime_pnl": {},
            "available": False,
            "neutral_default": WEIGHTS["regime"] / 2.0,
        }
    ratio = positive_regimes / total_regimes
    points = WEIGHTS["regime"] * ratio
    return points, {
        "regime_pnl": metrics["regime_pnl"],
        "regimes_positive": positive_regimes,
        "regimes_total": total_regimes,
        "available": True,
    }


_COMPONENTS = (
    ("sampling", _sampling_component),
    ("win_rate", _win_rate_component),
    ("pnl", _pnl_component),
    ("drawdown", _drawdown_component),
    ("consistency", _consistency_component),
    ("risk_per_trade", _risk_per_trade_component),
    ("martingale", _martingale_component),
    ("diversification", _diversification_component),
    ("regime", _regime_component),
)


def compute_breakdown(metrics: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Run all nine components on a pre-computed metrics dict.
    Returns (total_score, breakdown_dict) where breakdown_dict maps each
    component name to {"score": float, "weight": float, **detail}.
    """
    breakdown: dict[str, Any] = {}
    total = 0.0
    for name, fn in _COMPONENTS:
        points, detail = fn(metrics)
        weight = WEIGHTS[name]
        capped = max(0.0, min(points, weight))
        breakdown[name] = {"score": capped, "weight": weight, **detail}
        total += capped
    total = max(0.0, min(total, 100.0))
    return total, breakdown


def compute_score(
    fills_df: pd.DataFrame,
    window_days: int,
    account_value: float | None = None,
    *,
    now: datetime | None = None,
    market_daily_df: pd.DataFrame | None = None,
) -> tuple[float, dict[str, Any]]:
    """
    Top-level entrypoint: build metrics for the window then run the nine
    scoring components. Returns (score_in_0_100, breakdown_dict).

    Pure: no Django/ORM side effects. Same inputs => same outputs regardless
    of caller (PRD §15 — todo score recalculável a partir do fill bruto).
    """
    metrics = compute_metrics_window(
        fills_df,
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
    return score, breakdown


def score_with_classification(
    fills_df: pd.DataFrame,
    window_days: int,
    account_value: float | None = None,
    *,
    now: datetime | None = None,
    market_daily_df: pd.DataFrame | None = None,
) -> tuple[float, str, dict[str, Any]]:
    """Convenience wrapper returning (score, classification, breakdown)."""
    score, breakdown = compute_score(
        fills_df,
        window_days,
        account_value,
        now=now,
        market_daily_df=market_daily_df,
    )
    return score, classify(score), breakdown


# Exposed so services.py can persist per-window intermediate metrics.
__all__ = [
    "WEIGHTS",
    "CLASSIFICATION_BANDS",
    "classify",
    "compute_score",
    "compute_breakdown",
    "score_with_classification",
]


# Avoid unused-import warnings in static analyzers; numpy is used by metrics
# but kept imported here so score.py stays a stable re-export surface.
_ = np
