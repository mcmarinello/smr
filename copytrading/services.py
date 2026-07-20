"""
Service layer for the Copy Trading simulator (PRD §Sprint 8).

process_copy_signals   — called after a tracking cycle for one wallet;
                         looks up active CopyTradingTargets that follow it
                         and replays the wallet's recent fills through the
                         simulator to open/close paper trades.
get_profile_performance — aggregates a profile's SimulatedTrade history
                         into a PnL summary, win rate, open PnL, and an
                         equity-curve time series for the dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q, Sum

from wallets.models import Fill, Wallet

from .models import CopyTradingProfile, CopyTradingTarget, SimulatedTrade
from .simulator import compute_virtual_equity, simulate_close, simulate_open

logger = logging.getLogger(__name__)

# Lookback for fills to replay on each call. Larger than the tracking beat
# interval (5m) so a delayed/missing beat still catches up; small enough to
# stay cheap on hot wallets.
SIGNAL_LOOKBACK_MINUTES = 30


def process_copy_signals(wallet_address: str) -> dict[str, Any]:
    """
    Replay the wallet's recent fills through every active profile that has
    it as a CopyTradingTarget. Idempotent: a fill is only simulated once
    per (profile, fill) because we track processed fills via the
    SimulatedTrade.fill_source FK.

    Returns {address, profiles_touched, opens, closes}.
    """
    address = wallet_address.strip().lower()
    try:
        wallet = Wallet.objects.get(address=address)
    except Wallet.DoesNotExist:
        logger.debug("process_copy_signals: wallet %s not found", address)
        return {"address": address, "profiles_touched": 0, "opens": 0, "closes": 0}

    targets = list(
        CopyTradingTarget.objects.select_related("profile")
        .filter(wallet=wallet, is_active=True, profile__is_active=True)
    )
    if not targets:
        return {"address": address, "profiles_touched": 0, "opens": 0, "closes": 0}

    since = datetime.now(tz=timezone.utc) - timedelta(minutes=SIGNAL_LOOKBACK_MINUTES)
    recent_fills = list(
        Fill.objects.filter(wallet=wallet, timestamp__gte=since).order_by("timestamp")
    )
    if not recent_fills:
        return {"address": address, "profiles_touched": 0, "opens": 0, "closes": 0}

    opens = 0
    closes = 0
    touched_profiles: set[int] = set()
    for target in targets:
        o, c = _replay_fills_for_target(target, wallet, recent_fills)
        if o or c:
            touched_profiles.add(target.profile_id)
        opens += o
        closes += c

    logger.info(
        "process_copy_signals %s: %d profiles, %d opens, %d closes",
        address,
        len(touched_profiles),
        opens,
        closes,
    )
    return {
        "address": address,
        "profiles_touched": len(touched_profiles),
        "opens": opens,
        "closes": closes,
    }


def _replay_fills_for_target(
    target: CopyTradingTarget,
    wallet: Wallet,
    fills: list[Fill],
) -> tuple[int, int]:
    """
    Replay `fills` against one (profile, wallet) target. An `open` fill
    with no matching SimulatedTrade creates one (subject to the profile's
    max_concurrent_positions cap); a `close`/liquidation fill flattens the
    matching open trade at the fill's price.
    """
    profile = target.profile
    opens = 0
    closes = 0

    # Fills we've already simulated for this profile — keyed by oid via
    # the existing SimulatedTrade.fill_source rows. Using it keeps the
    # function idempotent across re-runs / overlapping beats.
    already_simulated_oids: set[int] = set(
        SimulatedTrade.objects.filter(
            profile=profile, wallet=wallet, fill_source__isnull=False
        ).values_list("fill_source_id", flat=True)
    )

    # Resolve once: fills that are pure opens (no open trade yet) vs closes.
    open_trade_oids_seen: set[int] = set()

    # Track trades currently open for this (profile, wallet) so close fills
    # can find the counterpart without re-querying on every iteration.
    open_trades_by_asset_side: dict[tuple[str, str], SimulatedTrade] = {
        (t.asset, t.side): t
        for t in SimulatedTrade.objects.filter(
            profile=profile, wallet=wallet, status=SimulatedTrade.Status.OPEN.value
        )
    }

    current_capital = _profile_virtual_capital(profile)

    for fill in fills:
        if fill.oid in already_simulated_oids and fill.oid not in open_trade_oids_seen:
            # Already accounted for — skip.
            if fill.direction == Fill.Direction.OPEN.value:
                continue
        side = _fill_trade_side(fill)
        if side is None:
            continue

        if fill.direction == Fill.Direction.OPEN.value:
            # Don't open twice on the same fill / same asset-side pair.
            if (fill.asset, side) in open_trades_by_asset_side:
                continue
            # Respect the profile's concurrency cap across all wallets.
            open_count = SimulatedTrade.objects.filter(
                profile=profile, status=SimulatedTrade.Status.OPEN.value
            ).count()
            if open_count >= profile.max_concurrent_positions:
                logger.debug(
                    "process_copy_signals: profile %s at max_concurrent_positions",
                    profile.id,
                )
                continue

            fields = simulate_open(
                strategy=profile.strategy,
                max_position_pct=profile.max_position_pct,
                allocation_pct=target.allocation_pct,
                fill_side=fill.side,
                fill_price=fill.price,
                current_capital=current_capital,
            )
            if fields is None:
                continue
            trade = SimulatedTrade.objects.create(
                profile=profile,
                wallet=wallet,
                asset=fill.asset,
                side=fields["side"],
                entry_price=fields["entry_price"],
                size_usd=fields["size_usd"],
                opened_at=fill.timestamp,
                status=SimulatedTrade.Status.OPEN.value,
                fill_source=fill,
            )
            open_trades_by_asset_side[(fill.asset, fields["side"])] = trade
            already_simulated_oids.add(fill.oid)
            open_trade_oids_seen.add(fill.oid)
            opens += 1
        else:
            # Close / liquidation: flatten the matching open trade, if any.
            trade = open_trades_by_asset_side.get((fill.asset, side))
            if trade is None:
                continue
            result = simulate_close(
                side=trade.side,
                entry_price=trade.entry_price,
                size_usd=trade.size_usd,
                exit_price=fill.price,
            )
            trade.exit_price = result["exit_price"]
            trade.pnl_usd = result["pnl_usd"]
            trade.status = (
                SimulatedTrade.Status.LIQUIDATED.value
                if fill.is_liquidation
                else SimulatedTrade.Status.CLOSED.value
            )
            trade.closed_at = fill.timestamp
            trade.save(
                update_fields=[
                    "exit_price",
                    "pnl_usd",
                    "status",
                    "closed_at",
                    "updated_at",
                ]
            )
            open_trades_by_asset_side.pop((fill.asset, side), None)
            closes += 1

    return opens, closes


def _fill_trade_side(fill: Fill) -> str | None:
    """Map a fill to the long/short trade side it belongs to."""
    if fill.direction not in (Fill.Direction.OPEN.value, Fill.Direction.CLOSE.value):
        return None
    if fill.direction == Fill.Direction.OPEN.value:
        return "long" if fill.side == Fill.Side.BUY.value else "short"
    return "long" if fill.side == Fill.Side.SELL.value else "short"


def _profile_virtual_capital(profile: CopyTradingProfile) -> Decimal:
    """
    Virtual capital the profile has available right now: initial_capital
    plus the realized PnL of every closed/liquidated trade. Open trades
    contribute unrealized PnL at mark time — see get_profile_performance
    for the marked-equity view used by the dashboard.
    """
    realized = (
        SimulatedTrade.objects.filter(
            profile=profile,
            status__in=[
                SimulatedTrade.Status.CLOSED.value,
                SimulatedTrade.Status.LIQUIDATED.value,
            ],
        ).aggregate(total=Sum("pnl_usd"))["total"]
        or Decimal("0")
    )
    return profile.initial_capital + (realized or Decimal("0"))


def get_profile_performance(profile_id: int) -> dict[str, Any]:
    """
    Aggregated view of a profile's paper performance for the dashboard.

    Returns:
        profile_id, strategy, initial_capital, current_equity,
        realized_pnl, open_pnl, total_trades, closed_trades,
        open_trades, wins, losses, win_rate, equity_curve
    """
    profile = CopyTradingProfile.objects.get(pk=profile_id)
    trades = list(
        SimulatedTrade.objects.filter(profile=profile).order_by("opened_at")
    )

    closed = [t for t in trades if t.status in ("closed", "liquidated")]
    open_ = [t for t in trades if t.status == "open"]

    realized_pnl = sum((t.pnl_usd or Decimal("0") for t in closed), Decimal("0"))
    # Mark open trades at entry (paper V1: no live mid feed) so the equity
    # curve reflects realized PnL only; a future Sprint can fetch mids.
    open_pnl = Decimal("0")

    current_equity = compute_virtual_equity(
        initial_capital=profile.initial_capital,
        closed_pnl=realized_pnl,
        open_trades=[
            {
                "side": t.side,
                "entry_price": t.entry_price,
                "size_usd": t.size_usd,
                "current_price": t.entry_price,  # V1: no live mark
            }
            for t in open_
        ],
    )

    wins = sum(1 for t in closed if (t.pnl_usd or 0) > 0)
    losses = sum(1 for t in closed if (t.pnl_usd or 0) < 0)
    decided = wins + losses
    win_rate = (wins / decided) if decided else Decimal("0")

    equity_curve = _build_equity_curve(profile, trades)

    return {
        "profile_id": profile.id,
        "name": profile.name,
        "strategy": profile.strategy,
        "initial_capital": profile.initial_capital,
        "current_equity": current_equity,
        "realized_pnl": realized_pnl,
        "open_pnl": open_pnl,
        "total_trades": len(trades),
        "closed_trades": len(closed),
        "open_trades": len(open_),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "equity_curve": equity_curve,
    }


def _build_equity_curve(
    profile: CopyTradingProfile, trades: list[SimulatedTrade]
) -> list[dict[str, Any]]:
    """
    Walking equity curve: start at initial_capital, apply each closed
    trade's realized PnL at its closed_at timestamp (open trades omitted
    since their mark is fixed at entry in V1).
    """
    curve: list[dict[str, Any]] = [
        {
            "t": profile.created_at.isoformat(),
            "equity": float(profile.initial_capital),
        }
    ]
    equity = profile.initial_capital
    for t in sorted(trades, key=lambda x: x.closed_at or x.opened_at):
        if t.status not in ("closed", "liquidated") or not t.closed_at:
            continue
        equity += (t.pnl_usd or Decimal("0"))
        curve.append(
            {"t": t.closed_at.isoformat(), "equity": float(equity)}
        )
    return curve


__all__ = [
    "process_copy_signals",
    "get_profile_performance",
    "SIGNAL_LOOKBACK_MINUTES",
]