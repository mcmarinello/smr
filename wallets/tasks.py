"""
Celery tasks for the Wallet Score Engine — queue: 'scoring' (routed in
settings.py via CELERY_TASK_ROUTES).

compute_all_scores   — periodic; recomputes scores for every active wallet
                       across all PRD §15.2 windows. Piggybacks the
                       deleveraged score computation (PRD §15.4) — every
                       WalletScore row gets `score_raw`, `score_deleveraged`
                       and `leverage_dependency_index` in the same run via
                       `compute_and_persist_scores`. No separate task needed.
compute_wallet_scores — one-shot per wallet; can be queued for a freshly
                        promoted target so its rank/labels refresh quickly
refresh_ranks         — lightweight job; re-ranks WalletScore rows only
                        (no calls to the HL API, no metrics recompute)
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from wallets.models import Wallet
from wallets.services import compute_and_persist_scores, recompute_ranks

# Local import to avoid a circular dependency at import time: the promotion
# module imports wallets.models, and wallets.tasks is imported eagerly by
# Celery autodiscovery. Doing it lazily inside the function would also work
# but keeping it at module level keeps the contract explicit.
from tracking.promotion import apply_promotion_demotion

logger = logging.getLogger(__name__)

# Toggle for the piggyback on compute_all_scores. Hidden behind a kwarg so a
# caller can recompute scores without touching promotion state if needed.
RUN_PROMOTION_DEMOTION_BY_DEFAULT = True


@shared_task(
    name="wallets.tasks.compute_all_scores",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def compute_all_scores(
    self,
    target_only: bool = False,
    run_promotion_demotion: bool = RUN_PROMOTION_DEMOTION_BY_DEFAULT,
) -> dict[str, Any]:
    """
    Iterate every active wallet (or only is_target wallets when target_only
    is True) and recompute metrics + scores for all windows. Scheduled
    periodically via CELERY_BEAT_SCHEDULE. Each wallet is processed inline so
    the task remains compact — for very large wallet universes, split into
    per-wallet sub-tasks by dispatching compute_wallet_scores instead.

    PRD §15.5 — promotion / demotion piggybacks on this run: after a wallet's
    scores are persisted we evaluate promotion/demotion against its
    WalletSettings. Set run_promotion_demotion=False to skip the gate.
    """
    qs = Wallet.objects.filter(is_active=True)
    if target_only:
        qs = qs.filter(is_target=True)

    total = 0
    failures: list[str] = []
    promotions: list[str] = []
    demotions: list[str] = []
    for wallet in qs.order_by("address"):
        total += 1
        try:
            compute_and_persist_scores(wallet)
            if run_promotion_demotion:
                result = apply_promotion_demotion(wallet)
                if result.get("promotion", {}).get("promoted"):
                    promotions.append(wallet.address)
                if result.get("demotion", {}).get("demoted"):
                    demotions.append(wallet.address)
        except Exception as exc:
            logger.exception("compute_all_scores wallet=%s failed: %s", wallet.address, exc)
            failures.append(wallet.address)

    logger.info(
        "compute_all_scores done: %d wallets processed, %d failures, %d promotions, %d demotions",
        total,
        len(failures),
        len(promotions),
        len(demotions),
    )
    return {
        "wallets_processed": total,
        "failures": failures,
        "target_only": target_only,
        "promotions": promotions,
        "demotions": demotions,
    }


@shared_task(
    name="wallets.tasks.compute_wallet_scores",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def compute_wallet_scores(self, address: str) -> dict[str, Any]:
    """
    Recompute scores for a single wallet identified by address. Useful as a
    follow-up when a wallet is promoted to target so its rows refresh
    immediately rather than waiting for the next beat tick.
    """
    try:
        wallet = Wallet.objects.get(address=address.strip().lower())
    except Wallet.DoesNotExist as exc:
        logger.error("compute_wallet_scores: wallet %s not found", address)
        raise self.retry(exc=exc, countdown=60) from exc

    try:
        summary = compute_and_persist_scores(wallet)
    except Exception as exc:
        logger.exception("compute_wallet_scores %s failed: %s", wallet.address, exc)
        countdown = 60 * (2**self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)

    logger.info("compute_wallet_scores %s done", wallet.address)
    return {"address": wallet.address, "summary": summary}


@shared_task(name="wallets.tasks.refresh_ranks")
def refresh_ranks() -> dict[str, Any]:
    """Lightweight periodic job that only reseats WalletScore.rank values."""
    updated = recompute_ranks()
    logger.info("refresh_ranks: %d score rows re-ranked", updated)
    return {"updated": updated}
