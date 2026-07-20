"""
Service layer for persisting wallet data.
Called by management commands, Celery tasks, and discovery/tracking workers.
All DB writes go through here — views and tasks should not use the ORM directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from django.db import transaction

from hyperliquid_client.client import HyperliquidClient
from hyperliquid_client.parsers import parse_fill, parse_position
from wallet_engine.score import compute_breakdown, classify
from wallet_engine.metrics import compute_metrics_window
from .models import (
    Wallet,
    Fill,
    Position,
    WalletMetricsWindow,
    WalletScore,
    days_for_window,
)

logger = logging.getLogger(__name__)


def get_or_create_wallet(address: str, source: str = "manual") -> tuple[Wallet, bool]:
    return Wallet.objects.get_or_create(
        address=address,
        defaults={"discovery_source": source},
    )


def persist_fills(wallet: Wallet, raw_fills: list[dict[str, Any]]) -> int:
    """
    Upserts fills for a wallet. Returns number of new fills created.
    Dedup key: oid (Hyperliquid order ID).
    """
    if not raw_fills:
        return 0

    existing_oids = set(Fill.objects.filter(wallet=wallet).values_list("oid", flat=True))
    new_fills = []
    for raw in raw_fills:
        try:
            parsed = parse_fill(raw, wallet.address)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping malformed fill for %s: %s — %r", wallet.address, exc, raw)
            continue
        if parsed["oid"] in existing_oids:
            continue
        new_fills.append(Fill(wallet=wallet, **parsed))

    if new_fills:
        Fill.objects.bulk_create(new_fills, ignore_conflicts=True)
        logger.info("Persisted %d new fills for %s", len(new_fills), wallet.address)

    wallet.last_seen = datetime.now(tz=timezone.utc)
    wallet.save(update_fields=["last_seen", "updated_at"])
    return len(new_fills)


def sync_positions(wallet: Wallet, clearinghouse: dict[str, Any]) -> None:
    """
    Replaces open positions for a wallet with the snapshot from clearinghouseState.
    Marks positions no longer in the snapshot as closed.
    """
    raw_positions = clearinghouse.get("assetPositions", [])

    with transaction.atomic():
        Position.objects.filter(wallet=wallet, status="open").update(status="closed")

        for raw in raw_positions:
            try:
                parsed = parse_position(raw)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Skipping malformed position for %s: %s", wallet.address, exc)
                continue
            if float(parsed["size"]) == 0:
                continue
            Position.objects.update_or_create(
                wallet=wallet,
                asset=parsed["asset"],
                status="open",
                defaults={**parsed, "opened_at": datetime.now(tz=timezone.utc)},
            )


def fetch_and_persist_wallet(address: str, source: str = "manual") -> dict[str, Any]:
    """
    Fetches fills + clearinghouseState for an address and persists both.
    Returns a summary dict. Used by the fetch_wallet management command and
    by Celery backfill tasks (discovery.tasks.backfill_wallet).

    `source` is only used when creating a new Wallet record; if the wallet
    already exists its discovery_source is not changed.
    """
    wallet, created = get_or_create_wallet(address, source=source)
    logger.info("%s wallet %s", "Created" if created else "Found", address)

    with HyperliquidClient() as client:
        raw_fills = client.user_fills(address)
        clearinghouse = client.clearinghouse_state(address)

    new_fill_count = persist_fills(wallet, raw_fills)
    sync_positions(wallet, clearinghouse)

    open_position_count = Position.objects.filter(wallet=wallet, status="open").count()
    return {
        "address": address,
        "created": created,
        "total_fills_returned": len(raw_fills),
        "new_fills_persisted": new_fill_count,
        "open_positions": open_position_count,
    }


# ---------------------------------------------------------------------------
# Score Engine persistence layer
# ---------------------------------------------------------------------------


_FILL_VALUE_FIELDS: tuple[str, ...] = (
    "id",
    "asset",
    "side",
    "price",
    "size",
    "fee",
    "closed_pnl",
    "timestamp",
    "is_liquidation",
    "oid",
    "direction",
    "start_position",
    "hash",
    "tid",
)


def fetch_account_value(wallet: Wallet) -> float:
    """
    Fetches the most recent accountValue from clearinghouseState (the equity
    proxy used by the PnL component — PRD §15.1 post-validation note). Falls
    back to `max(abs(total_pnl), 1)` so callers can always proceed even if the
    HL API is unavailable or the wallet has no open positions.
    """
    try:
        with HyperliquidClient() as client:
            cs = client.clearinghouse_state(wallet.address)
    except Exception as exc:
        logger.warning(
            "clearinghouseState failed for %s (%s) — falling back to PnL proxy",
            wallet.address,
            exc,
        )
        return _pnl_based_equity_fallback(wallet)

    margin = {}
    if isinstance(cs, dict):
        margin = cs.get("marginSummary") or {}
    av_raw = margin.get("accountValue", 0)
    try:
        av = float(av_raw)
    except (TypeError, ValueError):
        av = 0.0

    if av <= 0:
        return _pnl_based_equity_fallback(wallet)
    return av


def _pnl_based_equity_fallback(wallet: Wallet) -> float:
    from django.db.models import Sum

    pnl_sum = Fill.objects.filter(wallet=wallet).aggregate(s=Sum("closed_pnl"))["s"] or Decimal("0")
    return max(abs(float(pnl_sum)), 1.0)


def fills_to_dataframe(wallet: Wallet) -> pd.DataFrame:
    """
    Loads the wallet's persisted Fill rows into a pandas DataFrame that the
    pure wallet_engine functions understand.
    """
    rows = list(wallet.fills.all().values(*_FILL_VALUE_FIELDS))
    df = pd.DataFrame(rows, columns=list(_FILL_VALUE_FIELDS))
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"])
    return df


def _serialize_daily_returns(daily_returns: pd.Series) -> list[dict[str, Any]]:
    if daily_returns is None or daily_returns.empty:
        return []
    out = []
    for day, value in daily_returns.items():
        out.append(
            {
                "day": day.isoformat() if hasattr(day, "isoformat") else str(day),
                "return": float(value),
            }
        )
    return out


def compute_and_persist_scores(
    wallet: Wallet,
    *,
    market_daily_df: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Computes metrics + score for all five PRD §15.2 windows and persists them
    as WalletMetricsWindow + WalletScore rows (one per window, upserted).

    `market_daily_df` (optional) is BTC daily returns indexed by day, used
    by the market-regime correlation component.

    Returns a summary dict keyed by window -> {score, classification}.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    fills_df = fills_to_dataframe(wallet)
    account_value = fetch_account_value(wallet)

    results: dict[str, dict[str, Any]] = {}

    for window_label, label in WalletMetricsWindow.Window.choices:
        window_days = days_for_window(window_label)
        metrics = compute_metrics_window(
            fills_df,
            window_days=window_days,
            account_value=account_value,
            now=now,
            market_daily_df=market_daily_df,
        )
        score_value, breakdown = compute_breakdown(metrics)
        score_value = float(max(0.0, min(score_value, 100.0)))
        classification = classify(score_value)

        with transaction.atomic():
            mww, _ = WalletMetricsWindow.objects.update_or_create(
                wallet=wallet,
                window=window_label,
                defaults={
                    "computed_at": now,
                    "total_trades": metrics["total_trades"],
                    "wins": metrics["wins"],
                    "losses": metrics["losses"],
                    "total_pnl": Decimal(str(metrics["total_pnl"])),
                    "total_fees": Decimal(str(metrics["total_fees"])),
                    "account_value": Decimal(str(metrics["account_value"])),
                    "normalized_pnl": Decimal(str(metrics["normalized_pnl"])),
                    "max_drawdown": Decimal(str(metrics["max_drawdown"])),
                    "max_drawdown_pct": Decimal(str(metrics["max_drawdown_pct"])),
                    "current_drawdown_pct": Decimal(str(metrics["current_drawdown_pct"])),
                    "daily_returns_std": Decimal(str(metrics["daily_returns_std"])),
                    "avg_notional_ratio": Decimal(str(metrics["avg_notional_ratio"])),
                    "notional_ratio_std": Decimal(str(metrics["notional_ratio_std"])),
                    "martingale_severity": Decimal(str(metrics["martingale_severity"])),
                    "martingale_events": metrics["martingale_events"],
                    "assets_total_count": metrics["assets_total_count"],
                    "assets_positive_count": metrics["assets_positive_count"],
                    "asset_pnl_json": metrics["asset_pnl"],
                    "regime_pnl_json": metrics["regime_pnl"],
                    "daily_returns_json": _serialize_daily_returns(metrics["daily_returns"]),
                },
            )

            wallet_score, _ = WalletScore.objects.update_or_create(
                wallet=wallet,
                window=window_label,
                defaults={
                    "computed_at": now,
                    "score_raw": Decimal(str(score_value)),
                    "classification": classification,
                    "component_breakdown": breakdown,
                    "metrics_window": mww,
                    "rank": None,  # recompute_ranks sets it later
                },
            )

        results[window_label] = {
            "score": score_value,
            "classification": classification,
            "total_trades": metrics["total_trades"],
            "metrics_window_id": mww.id,
            "score_id": wallet_score.id,
        }

    recompute_ranks()
    return results


def recompute_ranks() -> int:
    """
    Recomputes rank for every WalletScore within its window, ordered by
    score_raw desc. Returns the number of rows re-ranked.
    """
    updated = 0
    for window, _ in WalletScore.Window.choices:
        scores = list(
            WalletScore.objects.filter(window=window).order_by("-score_raw").only("id", "window")
        )
        for idx, score_obj in enumerate(scores, start=1):
            if score_obj.rank != idx:
                score_obj.rank = idx
                score_obj.save(update_fields=["rank"])
            updated += 1
    return updated
