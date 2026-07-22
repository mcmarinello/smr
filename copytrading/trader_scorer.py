"""
Trader Scoring — rates traders 0-100 based on performance.

Ported from whale-copy/scoring/trader_scorer.py and adapted to
Django-compatible patterns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ScoreResult:
    """Result of scoring a trader."""

    score: int
    win_rate: float
    total_trades: int
    total_pnl: float
    avg_pnl: float
    profit_factor: float
    max_drawdown: float
    consistency: float  # coefficient of variation (lower = better)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "total_pnl": self.total_pnl,
            "avg_pnl": self.avg_pnl,
            "profit_factor": self.profit_factor,
            "max_drawdown": self.max_drawdown,
            "consistency": self.consistency,
        }


class TraderScorer:
    """
    Score a trader from 0-100.

    Weights:
      25 pts — win rate
      25 pts — profit factor
      20 pts — consistency (low variance of PnL)
      15 pts — total PnL positive
      15 pts — sample size (trade count)
    """

    @staticmethod
    def score(fills: List[dict]) -> ScoreResult:
        if not fills:
            return ScoreResult(0, 0, 0, 0, 0, 0, 0, 0)

        # Per-coin PnL aggregation
        pnls: list[float] = []
        for f in fills:
            pnl = float(f.get("closedPnl", 0))
            if pnl != 0:
                pnls.append(pnl)

        if not pnls:
            return ScoreResult(0, 0, len(fills), 0, 0, 0, 0, 0)

        total = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / total * 100 if total else 0
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / total if total else 0

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.01
        profit_factor = gross_profit / gross_loss if gross_loss else 999

        # Max drawdown
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = peak - cum
            max_dd = max(max_dd, dd)

        # Consistency (CV)
        if total > 1 and avg_pnl != 0:
            variance = sum((p - avg_pnl) ** 2 for p in pnls) / total
            std = variance**0.5
            cv = std / abs(avg_pnl)
        else:
            cv = 0

        # Scoring
        score = 0

        # Win rate (25 pts)
        if win_rate >= 60:
            score += 25
        elif win_rate >= 50:
            score += 18
        elif win_rate >= 40:
            score += 10
        elif win_rate >= 30:
            score += 5

        # Profit factor (25 pts)
        if profit_factor >= 2.0:
            score += 25
        elif profit_factor >= 1.5:
            score += 20
        elif profit_factor >= 1.2:
            score += 15
        elif profit_factor >= 1.0:
            score += 10

        # Consistency (20 pts)
        if cv < 0.8:
            score += 20
        elif cv < 1.2:
            score += 16
        elif cv < 2.0:
            score += 10
        elif cv < 3.0:
            score += 5

        # Total PnL (15 pts)
        if total_pnl > 0:
            score += 15
        elif total_pnl > -50:
            score += 5

        # Sample size (15 pts)
        if total >= 200:
            score += 15
        elif total >= 100:
            score += 12
        elif total >= 50:
            score += 8
        elif total >= 20:
            score += 5

        return ScoreResult(
            score=min(score, 100),
            win_rate=round(win_rate, 1),
            total_trades=total,
            total_pnl=round(total_pnl, 2),
            avg_pnl=round(avg_pnl, 2),
            profit_factor=round(profit_factor, 2),
            max_drawdown=round(max_dd, 2),
            consistency=round(cv, 2),
        )


__all__ = [
    "ScoreResult",
    "TraderScorer",
]
