"""
Celery tasks for the Alert Engine — queue: 'alerts' (routed in settings.py
via CELERY_TASK_ROUTES).

process_wallet_alerts       — chained after track_wallet_fills: evaluates
                             new_position / position_closed for the wallet
                             touched in the latest tracking cycle.
process_convergence_alert   — chained per detect_convergence cluster: fans
                             the cluster out to every participating wallet.
send_pending_notifications  — periodic; drains unsent Notifications through
                             the delivery channel (Telegram bot / interface
                             badge). V1 logs every dispatch; the Telegram
                             integration is the Sprint 6 stretch goal.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from wallets.models import Position, Wallet

from .services import evaluate_alerts_for_wallet, evaluate_convergence

logger = logging.getLogger(__name__)


@shared_task(
    name="alerts.tasks.process_wallet_alerts",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_wallet_alerts(
    self,
    wallet_address: str,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluates per-wallet alert rules after a tracking cycle. `summary` is the
    dict returned by `track_wallet_fills` (PRD §16.1) and is expected to
    carry `position_events` — a list of {action, position_id} records that
    let us dispatch `new_position` and `position_closed` triggers without
    re-querying the whole position table.
    """
    summary = summary or {}
    events = summary.get("position_events") or []
    if not events:
        logger.debug(
            "process_wallet_alerts %s: no position events", wallet_address
        )
        return {"address": wallet_address, "fired": 0}

    address = wallet_address.strip().lower()
    try:
        wallet = Wallet.objects.get(address=address)
    except Wallet.DoesNotExist:
        logger.warning("process_wallet_alerts: wallet %s not found", address)
        return {"address": address, "fired": 0}

    fired = 0
    for event in events:
        action = event.get("action")
        position_id = event.get("position_id")
        if position_id is None or action is None:
            continue
        try:
            position = Position.objects.get(pk=position_id, wallet=wallet)
        except Position.DoesNotExist:
            logger.debug("process_wallet_alerts: position %s gone", position_id)
            continue

        if action == "opened":
            fired += _evaluate(
                wallet_address,
                "new_position",
                {
                    "asset": position.asset,
                    "side": position.side,
                    "size": str(position.size),
                    "entry_price": str(position.entry_price),
                    "position_id": position.id,
                },
                event,
            )
        elif action == "closed":
            fired += _evaluate(
                wallet_address,
                "position_closed",
                {
                    "asset": position.asset,
                    "side": position.side,
                    "position_id": position.id,
                    "closed_at": (
                        position.closed_at.isoformat()
                        if position.closed_at
                        else None
                    ),
                    "is_liquidation": bool(event.get("is_liquidation", False)),
                },
                event,
            )
    return {"address": address, "fired": fired}


def _evaluate(address: str, event_type: str, event_data: dict, source_event: dict) -> int:
    """Wrap evaluate_alerts_for_wallet so asset_specific rules see the
    underlying event in metadata."""
    payload = {"source_event": event_type, **event_data}
    return len(evaluate_alerts_for_wallet(address, event_type, payload))


@shared_task(name="alerts.tasks.process_convergence_alert")
def process_convergence_alert(cluster: dict[str, Any]) -> dict[str, Any]:
    """
    Receives one cluster dict as returned by detect_convergence (PRD §16.2)
    and evaluates the convergence trigger against every participating wallet.
    """
    asset = cluster.get("asset", "")
    side = cluster.get("side", "")
    wallets_ = cluster.get("wallets", []) or []
    if not asset or not wallets_:
        return {"asset": asset, "side": side, "fired": 0}

    notifications = evaluate_convergence(
        asset=asset,
        side=side,
        wallet_addresses=list(wallets_),
        first_seen=cluster.get("first_seen"),
        last_seen=cluster.get("last_seen"),
    )
    return {"asset": asset, "side": side, "fired": len(notifications)}


@shared_task(name="alerts.tasks.send_pending_notifications")
def send_pending_notifications(*, batch_size: int = 200) -> dict[str, Any]:
    """
    PRD §17 — drains the unsent Notification queue. For V1 every delivery is
    logged (Telegram integration is the Sprint 6 stretch goal); the receipt
    is recorded via `sent_at` so the next cycle resumes cleanly.
    """
    from .models import Notification

    qs = (
        Notification.objects.select_related("user", "alert_rule", "wallet")
        .filter(sent_at__isnull=True)
        .order_by("created_at")[:batch_size]
    )
    dispatched = 0
    for notif in qs:
        _dispatch(notif)
        dispatched += 1
    logger.info(
        "send_pending_notifications: dispatched %d notifications", dispatched
    )
    return {"dispatched": dispatched}


def _dispatch(notif: Notification) -> None:
    """V1 dispatcher — log only. Sprint 6 stretch: Telegram bot when the
    rule's channel is telegram/both and TELEGRAM_BOT_TOKEN is configured."""
    logger.info(
        "ALERT OUT user=%s notif=%s level=%s event=%s wallet=%s title=%s",
        notif.user_id,
        notif.id,
        notif.level,
        notif.event_type,
        notif.wallet.address if notif.wallet_id else "-",
        notif.title,
    )
    notif.sent_at = __import__("django.utils.timezone", fromlist=["now"]).now()
    notif.save(update_fields=["sent_at"])