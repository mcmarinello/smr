"""
Service layer for the Alert Engine (PRD §17).

evaluate_alerts_for_wallet       — general dispatcher; matches every active
                                   AlertRule against one (wallet, event_type)
                                   pair, runs the per-condition match logic,
                                   applies the cooldown dedup and persists
                                   one Notification per firing rule.
evaluate_new_position            — convenience wrapper around the dispatcher.
evaluate_position_closed         — convenience wrapper.
evaluate_score_cross             — fires on threshold crossings (up or down).
evaluate_convergence             — fans out a convergence cluster to every
                                   participating wallet.

Dedup is governed by settings.ALERT_DEDUP_COOLDOWN_SECONDS (default 3600).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from wallets.models import Wallet

from .models import AlertHistory, AlertRule, Notification

logger = logging.getLogger(__name__)

# PRD §17 — default cooldown when settings.ALERT_DEDUP_COOLDOWN_SECONDS is not
# provided. Importing settings lazily inside the function keeps the module
# reload-safe during tests that override the setting at runtime.
DEFAULT_COOLDOWN_SECONDS = 3600


def _cooldown_seconds() -> int:
    from django.conf import settings

    return getattr(settings, "ALERT_DEDUP_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)


def _matching_rules(
    wallet: Wallet,
    condition_type: str,
    asset: str | None,
) -> list[AlertRule]:
    """
    All active AlertRules that apply to (wallet, condition_type, asset):
      - condition_type matches exactly, OR is asset_specific (asset-scoped
        catch-all across the other triggers — PRD §17 last row).
      - wallet is NULL (= "all targets", restricted to is_target wallets) or
        explicitly equals this wallet.
      - asset_filter, when set, contains the uppercased asset symbol.
    """
    qs = AlertRule.objects.filter(is_active=True)
    # condition_type: exact OR asset_specific acts as catch-all (any trigger)
    # for the asset matching that asset_filter value.
    qs = qs.filter(
        Q(condition_type=condition_type)
        | Q(condition_type=AlertRule.ConditionType.ASSET_SPECIFIC.value)
    )
    # wallet scoping: NULL rule targets every is_target wallet.
    if not wallet.is_target:
        qs = qs.filter(wallet=wallet)
    else:
        qs = qs.filter(Q(wallet__isnull=True) | Q(wallet=wallet))

    rules: list[AlertRule] = []
    upper_asset = (asset or "").upper()
    for rule in qs.select_related("user", "wallet"):
        allowed = rule.asset_set
        if allowed and upper_asset not in allowed:
            continue
        rules.append(rule)
    return rules


def _is_duplicate(rule: AlertRule, wallet: Wallet | None, event_type: str, asset: str) -> bool:
    """True if this (rule, wallet, event_type, asset) already fired in cooldown."""
    since = timezone.now() - timedelta(seconds=_cooldown_seconds())
    qs = AlertHistory.objects.filter(
        alert_rule=rule,
        event_type=event_type,
        fired_at__gte=since,
    )
    if wallet is not None:
        qs = qs.filter(wallet=wallet)
    else:
        qs = qs.filter(wallet__isnull=True)
    if asset:
        qs = qs.filter(asset=asset)
    return qs.exists()


def _level_for(event_type: str) -> str:
    if event_type in (
        AlertRule.ConditionType.CONVERGENCE.value,
        AlertRule.ConditionType.SCORE_THRESHOLD_CROSS.value,
    ):
        return Notification.Level.WARNING.value
    if event_type == AlertRule.ConditionType.POSITION_CLOSED.value:
        return Notification.Level.WARNING.value
    return Notification.Level.INFO.value


def _create_firing(
    rule: AlertRule,
    wallet: Wallet | None,
    event_type: str,
    asset: str,
    title: str,
    body: str,
    metadata: dict[str, Any],
    level: str | None = None,
) -> Notification | None:
    """
    Persist Notification + AlertHistory for one matched rule, after the dedup
    gate. Returns the Notification (or None when dedup blocked the firing).
    Wrapped in a transaction so a partial crash doesn't leave orphan history
    rows that would silently swallow the next legitimate firing.
    """
    if _is_duplicate(rule, wallet, event_type, asset):
        logger.debug(
            "alert dedup skip rule=%s wallet=%s event=%s asset=%s",
            rule.id,
            wallet.address if wallet else "-",
            event_type,
            asset or "-",
        )
        return None

    with transaction.atomic():
        notification = Notification.objects.create(
            user=rule.user,
            alert_rule=rule,
            wallet=wallet,
            title=title,
            body=body,
            level=level or _level_for(event_type),
            event_type=event_type,
            metadata=metadata,
        )
        AlertHistory.objects.create(
            alert_rule=rule,
            wallet=wallet,
            event_type=event_type,
            asset=asset,
            notification=notification,
        )
    logger.info(
        "alert fired rule=%s wallet=%s event=%s asset=%s notif=%s",
        rule.id,
        wallet.address if wallet else "-",
        event_type,
        asset or "-",
        notification.id,
    )
    return notification


def evaluate_alerts_for_wallet(
    wallet_address: str,
    event_type: str,
    event_data: dict[str, Any] | None = None,
) -> list[Notification]:
    """
    General dispatcher used by every event-specific helper. Resolves the
    wallet, finds matching active rules, applies the per-condition match
    logic (threshold / convergence min-wallets) and persists one Notification
    per firing rule. Returns the list of created Notifications (after dedup).
    """
    event_data = event_data or {}
    address = wallet_address.strip().lower()
    try:
        wallet = Wallet.objects.get(address=address)
    except Wallet.DoesNotExist:
        logger.warning("evaluate_alerts_for_wallet: wallet %s not found", address)
        return []

    asset = event_data.get("asset")
    rules = _matching_rules(wallet, event_type, asset)
    notifications: list[Notification] = []

    for rule in rules:
        title, body, level, meta = _build_event_payload(
            rule, wallet, event_type, event_data
        )
        if meta is None:
            # Rule-specific predicate (threshold / min-wallets) filtered out.
            continue
        notif = _create_firing(
            rule=rule,
            wallet=wallet,
            event_type=event_type,
            asset=asset or "",
            title=title,
            body=body,
            metadata=meta,
            level=level,
        )
        if notif is not None:
            notifications.append(notif)
    return notifications


def _build_event_payload(
    rule: AlertRule,
    wallet: Wallet,
    event_type: str,
    event_data: dict[str, Any],
) -> tuple[str, str, str | None, dict[str, Any] | None]:
    """
    Returns (title, body, level_overrides, metadata). metadata is None when
    the rule's per-condition predicate rejects the event (no Notification).
    """
    if event_type == AlertRule.ConditionType.NEW_POSITION.value:
        return _payload_new_position(wallet, event_data)
    if event_type == AlertRule.ConditionType.POSITION_CLOSED.value:
        return _payload_position_closed(wallet, event_data)
    if event_type == AlertRule.ConditionType.SCORE_THRESHOLD_CROSS.value:
        return _payload_score_cross(rule, wallet, event_data)
    if event_type == AlertRule.ConditionType.CONVERGENCE.value:
        return _payload_convergence(rule, wallet, event_data)
    if event_type == AlertRule.ConditionType.ASSET_SPECIFIC.value:
        # asset_specific rules piggyback on whatever underlying event
        # surfaced them; describe it generically.
        return _payload_asset_specific(wallet, event_data)
    return None  # type: ignore[return-value]


def _payload_new_position(wallet, ev) -> tuple[str, str, None, dict]:
    asset = ev.get("asset", "?")
    side = ev.get("side", "?")
    size = ev.get("size", "?")
    entry = ev.get("entry_price", "?")
    title = f"Nova posição: {wallet.address[:8]} {side} {asset}"
    body = (
        f"Carteira {wallet.address} abriu {side} {size} {asset} @ {entry}."
    )
    meta = {"wallet": wallet.address, "asset": asset, "side": side, **ev}
    return title, body, None, meta


def _payload_position_closed(wallet, ev) -> tuple[str, str, str, dict]:
    asset = ev.get("asset", "?")
    side = ev.get("side", "?")
    is_liq = ev.get("is_liquidation", False)
    level = Notification.Level.CRITICAL.value if is_liq else Notification.Level.WARNING.value
    action = "liquidada" if is_liq else "fechada"
    title = f"Posição {action}: {wallet.address[:8]} {side} {asset}"
    body = f"Carteira {wallet.address} {action} a posição {side} {asset}."
    meta = {"wallet": wallet.address, "asset": asset, "side": side, **ev}
    return title, body, level, meta


def _payload_score_cross(rule, wallet, ev) -> tuple[str, str, str, dict | None]:
    """
    Fires when the rule.threshold sits strictly between old_score and new_score
    (crossing up or down). PRD §17 example: "avisa quando uma carteira que sigo
    cair de elite para bom".
    """
    threshold = rule.threshold
    try:
        old = Decimal(str(ev.get("old_score")))
        new = Decimal(str(ev.get("new_score")))
    except (TypeError, ValueError):
        return "", "", None, None
    if threshold is None:
        return "", "", None, None
    direction = None
    if old < threshold <= new:
        direction = "up"
    elif old >= threshold > new:
        direction = "down"
    if direction is None:
        return "", "", None, None
    window = ev.get("window", "?")
    title = f"Score cruzou {threshold} ({direction}): {wallet.address[:8]} [{window}]"
    body = (
        f"Carteira {wallet.address} score {window} foi de {old} → {new} "
        f"(limiar {threshold}, direção {direction})."
    )
    meta = {
        "wallet": wallet.address,
        "window": window,
        "old_score": str(old),
        "new_score": str(new),
        "threshold": str(threshold),
        "direction": direction,
    }
    return title, body, Notification.Level.WARNING.value, meta


def _payload_convergence(rule, wallet, ev) -> tuple[str, str, str, dict | None]:
    """
    PRD §17 — convergence rule may set its own min_wallets via `threshold`.
    When set, we require wallet_count >= rule.threshold (else skip this rule
    without firing). When NULL, the system default is honored upstream and
    we fire for any cluster.
    """
    count = ev.get("wallet_count", 0)
    if rule.threshold is not None:
        try:
            min_required = Decimal(str(rule.threshold))
        except (TypeError, ValueError):
            min_required = None
        if min_required is not None and Decimal(str(count)) < min_required:
            return "", "", None, None
    asset = ev.get("asset", "?")
    side = ev.get("side", "?")
    wallets_list = ev.get("wallets", [])
    title = f"Convergência: {count} wallets {side} {asset}"
    body = (
        f"Convergência de smart money em {asset} ({side}): "
        f"{count} carteiras-alvo — {', '.join(wallets_list)}."
    )
    meta = {
        "wallet": wallet.address,
        "asset": asset,
        "side": side,
        "wallet_count": count,
        "wallets": wallets_list,
        "first_seen": ev.get("first_seen"),
        "last_seen": ev.get("last_seen"),
    }
    return title, body, Notification.Level.CRITICAL.value, meta


def _payload_asset_specific(wallet, ev) -> tuple[str, str, None, dict]:
    """
    An asset_specific rule fires for whatever underlying event surfaced it
    (new_position / position_closed / convergence). The asset filter itself
    is enforced in `_matching_rules`, so here we just describe the event.
    """
    event_type = ev.get("source_event", "event")
    asset = ev.get("asset", "?")
    title = f"Alerta de ativo ({asset}): {wallet.address[:8]} {event_type}"
    body = (
        f"Evento {event_type} em {asset} — carteira {wallet.address}."
    )
    meta = {"wallet": wallet.address, **ev}
    return title, body, None, meta


# ---------------------------------------------------------------------------
# Event-specific convenience helpers (PRD §17 — each one a public entrypoint)
# ---------------------------------------------------------------------------


def evaluate_new_position(wallet: Wallet, position) -> list[Notification]:
    """PRD §17 — `new_position` trigger."""
    return evaluate_alerts_for_wallet(
        wallet.address,
        AlertRule.ConditionType.NEW_POSITION.value,
        {
            "asset": position.asset,
            "side": position.side,
            "size": str(position.size),
            "entry_price": str(position.entry_price),
            "position_id": position.id,
        },
    )


def evaluate_position_closed(wallet: Wallet, position) -> list[Notification]:
    """PRD §17 — `position_closed` trigger (covers liquidations)."""
    return evaluate_alerts_for_wallet(
        wallet.address,
        AlertRule.ConditionType.POSITION_CLOSED.value,
        {
            "asset": position.asset,
            "side": position.side,
            "position_id": position.id,
            "closed_at": position.closed_at.isoformat() if position.closed_at else None,
            "is_liquidation": bool(getattr(position, "_is_liquidation", False)),
        },
    )


def evaluate_score_cross(
    wallet: Wallet,
    old_score: float | Decimal | None,
    new_score: float | Decimal | None,
    *,
    window: str = "7d",
) -> list[Notification]:
    """PRD §17 — `score_threshold_cross` trigger. Either score being None
    short-circuits: a threshold can only be "crossed" when we have both."""
    if old_score is None or new_score is None:
        return []
    return evaluate_alerts_for_wallet(
        wallet.address,
        AlertRule.ConditionType.SCORE_THRESHOLD_CROSS.value,
        {
            "window": window,
            "old_score": str(old_score),
            "new_score": str(new_score),
        },
    )


def evaluate_convergence(
    asset: str,
    side: str,
    wallet_addresses: list[str],
    *,
    first_seen: str | None = None,
    last_seen: str | None = None,
) -> list[Notification]:
    """
    PRD §17 — `convergence` trigger. Fans one cluster out to every
    participating wallet (so each target's alert rules get evaluated
    independently). Per-wallet dedup keeps this spam-free across
    consecutive detect_convergence cycles.
    """
    fired: list[Notification] = []
    wallet_count = len(wallet_addresses)
    event_data = {
        "asset": asset,
        "side": side,
        "wallets": list(wallet_addresses),
        "wallet_count": wallet_count,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "source_event": AlertRule.ConditionType.CONVERGENCE.value,
    }
    for address in wallet_addresses:
        fired.extend(
            evaluate_alerts_for_wallet(
                address,
                AlertRule.ConditionType.CONVERGENCE.value,
                event_data,
            )
        )
    return fired