"""
Promotion / demotion logic (PRD §15.5).

check_promotion  — score_raw crosses promotion_threshold in 7d OR 30d window.
check_demotion   — score_raw below demotion_threshold for N consecutive
                   recalculations (hysteresis to avoid flapping).
apply_promotion_demotion — convenience that runs both and persists the result.

Threshold / N come from WalletSettings (per-wallet) when present, falling back
to module defaults. The PRD leaves whether to gate on score_raw or
score_deleveraged as a user preference — V1 defaults to score_raw; this can
be extended in WalletSettings without changing the public API.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from django.db import transaction

from wallets.models import Wallet, Window, WalletScore, WalletSettings

logger = logging.getLogger(__name__)


# PRD §15.5 — defaults; overridable per-wallet via WalletSettings.
DEFAULT_PROMOTION_THRESHOLD = 70
DEFAULT_DEMOTION_THRESHOLD = 55
DEFAULT_DEMOTION_CONSECUTIVE_REQUIRED = 3

# Windows considered for promotion: a single crossing in any of these is
# sufficient evidence that the wallet deserves target status.
PROMOTION_WINDOWS: tuple[str, ...] = (Window.D7.value, Window.D30.value)


def get_settings(wallet: Wallet) -> WalletSettings:
    """Auto-creates WalletSettings on first access so callers always get one."""
    settings, _ = WalletSettings.objects.get_or_create(wallet=wallet)
    return settings


def _latest_score(wallet: Wallet, window: str) -> WalletScore | None:
    return (
        WalletScore.objects.filter(wallet=wallet, window=window)
        .order_by("-computed_at")
        .first()
    )


def check_promotion(wallet: Wallet) -> dict[str, Any]:
    """
    PRD §15.5 — promote when score_raw crosses the per-wallet promotion
    threshold in either the 7d or 30d window. Idempotent: a wallet already
    flagged as target is left untouched. Records the score at promotion and
    reason text for auditability.
    """
    settings = get_settings(wallet)
    threshold = settings.promotion_threshold

    crossing_window: str | None = None
    crossing_score: float | None = None
    for window in PROMOTION_WINDOWS:
        score_obj = _latest_score(wallet, window)
        if score_obj is None:
            continue
        if float(score_obj.score_raw) >= threshold:
            crossing_window = window
            crossing_score = float(score_obj.score_raw)
            break

    if crossing_window is None:
        return {
            "address": wallet.address,
            "promoted": False,
            "threshold": threshold,
        }

    if wallet.is_target:
        # Already a target — no-op but report the crossing for observability.
        return {
            "address": wallet.address,
            "promoted": False,
            "already_target": True,
            "threshold": threshold,
            "window": crossing_window,
            "score": crossing_score,
        }

    reason = (
        f"score_raw={crossing_score:.2f} in {crossing_window} window "
        f">= promotion_threshold={threshold}"
    )
    now = datetime.now(tz=timezone.utc)
    with transaction.atomic():
        Wallet.objects.filter(pk=wallet.pk).update(
            is_target=True,
            promoted_at=now,
            promoted_reason=reason,
            score_at_promotion=int(round(crossing_score)),
            demotion_consecutive_count=0,
            updated_at=now,
        )

    logger.info("Promoted %s: %s", wallet.address, reason)
    return {
        "address": wallet.address,
        "promoted": True,
        "threshold": threshold,
        "window": crossing_window,
        "score": crossing_score,
        "reason": reason,
    }


def check_demotion(wallet: Wallet) -> dict[str, Any]:
    """
    PRD §15.5 — demote only when score_raw stays below the demotion threshold
    for N consecutive recalculations (hysteresis). A wallet already demoted /
    never promoted is left alone. The "current" score is the max across the
    promotion windows — if either is above the demotion threshold the streak
    resets to 0 and the wallet survives.
    """
    if not wallet.is_target:
        return {"address": wallet.address, "demoted": False, "is_target": False}

    settings = get_settings(wallet)
    threshold = settings.demotion_threshold
    required = settings.demotion_consecutive_required

    scores = [_latest_score(wallet, w) for w in PROMOTION_WINDOWS]
    best = max(
        (float(s.score_raw) for s in scores if s is not None),
        default=None,
    )

    if best is None:
        # No scores yet — cannot demote without evidence.
        return {
            "address": wallet.address,
            "demoted": False,
            "reason": "no_scores",
        }

    if best >= threshold:
        # Streak broken — reset counter, keep target status.
        if wallet.demotion_consecutive_count != 0:
            Wallet.objects.filter(pk=wallet.pk).update(demotion_consecutive_count=0)
        return {
            "address": wallet.address,
            "demoted": False,
            "best_score": best,
            "threshold": threshold,
            "streak_reset": True,
        }

    new_count = wallet.demotion_consecutive_count + 1
    if new_count < required:
        Wallet.objects.filter(pk=wallet.pk).update(demotion_consecutive_count=new_count)
        return {
            "address": wallet.address,
            "demoted": False,
            "best_score": best,
            "threshold": threshold,
            "consecutive_count": new_count,
            "consecutive_required": required,
        }

    reason = (
        f"score_raw below {threshold} for {new_count} consecutive recalculations "
        f"(best={best:.2f})"
    )
    now = datetime.now(tz=timezone.utc)
    with transaction.atomic():
        Wallet.objects.filter(pk=wallet.pk).update(
            is_target=False,
            demotion_consecutive_count=0,
            promoted_reason=wallet.promoted_reason + " | demoted: " + reason
            if wallet.promoted_reason
            else "demoted: " + reason,
            updated_at=now,
        )

    logger.info("Demoted %s: %s", wallet.address, reason)
    return {
        "address": wallet.address,
        "demoted": True,
        "best_score": best,
        "threshold": threshold,
        "consecutive_count": new_count,
        "reason": reason,
    }


def apply_promotion_demotion(wallet: Wallet) -> dict[str, Any]:
    """
    Run both gates for one wallet and return a combined summary. Safe to call
    after every score recomputation. Promotion is evaluated first so a wallet
    can be promoted and a demotion streak reset in the same pass.
    """
    promo = check_promotion(wallet)
    # Re-read to pick up the is_target / streak changes from promotion.
    wallet.refresh_from_db()
    demo = check_demotion(wallet)
    return {"address": wallet.address, "promotion": promo, "demotion": demo}