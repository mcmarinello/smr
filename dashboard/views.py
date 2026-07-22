"""
Dashboard views (PRD §18) — read-only aggregations over wallets, scores,
positions, fills, notifications, and discovery status.

All labels are pt-BR per CLAUDE.md.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from alerts.models import Notification
from discovery.services import get_status_summary
from wallets.models import (
    Fill,
    Position,
    Wallet,
    WalletScore,
    Window,
    WalletSettings,
)

# PRD §15.1 — component display order for the score breakdown table.
COMPONENT_LABELS: list[tuple[str, str]] = [
    ("sampling", "Amostragem"),
    ("win_rate", "Win Rate"),
    ("pnl", "PnL/ROI"),
    ("drawdown", "Drawdown"),
    ("consistency", "Consistência"),
    ("risk_per_trade", "Risco por Trade"),
    ("martingale", "Martingale"),
    ("diversification", "Diversificação"),
    ("regime", "Correlação de Regime"),
]

# PRD §15.1 — max points each component contributes. Mirrors
# wallet_engine.score.WEIGHTS but kept here to avoid importing the engine
# inside this thin presentation layer.
COMPONENT_WEIGHTS: dict[str, int] = {
    "sampling": 10,
    "win_rate": 15,
    "pnl": 20,
    "drawdown": 15,
    "consistency": 15,
    "risk_per_trade": 10,
    "martingale": 5,
    "diversification": 5,
    "regime": 5,
}


@login_required
def dashboard_home(request):
    """Overview page — KPIs + recent alerts (PRD §18.1)."""
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_wallets = Wallet.objects.filter(is_active=True).count()
    active_targets = Wallet.objects.filter(is_active=True, is_target=True).count()
    alerts_today = Notification.objects.filter(
        created_at__gte=today_start, user=request.user
    ).count()
    unread_alerts = Notification.objects.filter(
        read=False, user=request.user
    ).count()

    discovery_status = get_status_summary()

    recent_alerts = (
        Notification.objects.filter(user=request.user)
        .select_related("wallet", "alert_rule")
        .order_by("-created_at")[:10]
    )

    context = {
        "total_wallets": total_wallets,
        "active_targets": active_targets,
        "alerts_today": alerts_today,
        "unread_alerts": unread_alerts,
        "discovery_status": discovery_status,
        "recent_alerts": recent_alerts,
    }
    return render(request, "dashboard/dashboard_home.html", context)


@login_required
def discovery_ranking(request):
    """Ranking table (PRD §18.2) — filters by window, classification, source."""
    window = request.GET.get("window", Window.D7.value)
    classification = request.GET.get("classification", "")
    source = request.GET.get("source", "")
    sort = request.GET.get("sort", "-score_raw")
    target_only = request.GET.get("target_only", "") == "1"

    # Validate the window value.
    valid_windows = {w.value for w in Window}
    if window not in valid_windows:
        window = Window.D7.value

    # Allowed sort columns — prefix '-' for descending. Guard against arbitrary
    # sort injection by whitelisting.
    allowed_sorts = {
        "score_raw",
        "-score_raw",
        "score_deleveraged",
        "-score_deleveraged",
        "leverage_dependency_index",
        "-leverage_dependency_index",
        "rank",
        "-rank",
        "wallet__address",
        "-wallet__address",
    }
    if sort not in allowed_sorts:
        sort = "-score_raw"

    # Pre-compute the bare field name and the direction ('asc'/'desc') so the
    # template can render sortable headers without fragile string slicing.
    if sort.startswith("-"):
        sort_field = sort[1:]
        sort_dir = "desc"
    else:
        sort_field = sort
        sort_dir = "asc"

    def flip(field: str) -> str:
        """Toggle the sort direction for a given column header."""
        return field if sort_field == field and sort_dir == "asc" else f"-{field}"

    sortable_fields = [
        ("wallet__address", "Wallet"),
        ("score_raw", "Score Raw"),
        ("score_deleveraged", "Score Delev."),
        ("leverage_dependency_index", "Lev. Dep."),
        ("rank", "Rank"),
    ]
    sortable_headers = [
        {"field": f, "label": lbl, "flip": flip(f)}
        for f, lbl in sortable_fields
    ]

    scores = (
        WalletScore.objects.filter(window=window)
        .select_related("wallet")
        .order_by(sort)
    )

    if classification:
        scores = scores.filter(classification=classification)
    if source:
        scores = scores.filter(wallet__discovery_source=source)
    if target_only:
        scores = scores.filter(wallet__is_target=True)

    context = {
        "scores": scores,
        "window": window,
        "windows": Window.choices,
        "classification": classification,
        "source": source,
        "sort": sort,
        "sort_field": sort_field,
        "sort_dir": sort_dir,
        "sortable_headers": sortable_headers,
        "target_only": target_only,
        "classifications": WalletScore.Classification.choices,
        "sources": Wallet.DiscoverySource.choices,
    }
    return render(request, "dashboard/discovery_ranking.html", context)


@login_required
def wallet_profile(request, address: str):
    """Deep view of a single wallet (PRD §18.3)."""
    address = address.strip().lower()
    wallet = get_object_or_404(Wallet, address=address)

    window = request.GET.get("window", Window.D7.value)
    valid_windows = {w.value for w in Window}
    if window not in valid_windows:
        window = Window.D7.value

    scores = WalletScore.objects.filter(wallet=wallet).select_related(
        "metrics_window"
    )
    current_score = scores.filter(window=window).first()
    scores_by_window = {s.window: s for s in scores}
    # Flatten to (window_value, window_label, score_or_None) for the template.
    window_scores = [
        (w.value, w.label, scores_by_window.get(w.value)) for w in Window
    ]

    positions = Position.objects.filter(wallet=wallet, status=Position.Status.OPEN)
    recent_fills = (
        Fill.objects.filter(wallet=wallet).order_by("-timestamp")[:50]
    )

    # PRD §18.3 — score breakdown table rows pre-rendered for both raw and
    # deleveraged breakdowns so the template just iterates.
    raw_breakdown = current_score.component_breakdown if current_score else {}
    delv_breakdown = (
        current_score.component_breakdown_deleveraged if current_score else {}
    )
    component_rows = []
    for key, label in COMPONENT_LABELS:
        raw = raw_breakdown.get(key, {}) if isinstance(raw_breakdown, dict) else({})
        delv = delv_breakdown.get(key, {}) if isinstance(delv_breakdown, dict) else({})
        component_rows.append(
            {
                "key": key,
                "label": label,
                "raw": raw.get("score", 0),
                "weight": raw.get("weight", COMPONENT_WEIGHTS.get(key, 0)),
                "delv": delv.get("score", 0),
            }
        )

    context = {
        "wallet": wallet,
        "window": window,
        "windows": Window.choices,
        "current_score": current_score,
        "window_scores": window_scores,
        "positions": positions,
        "recent_fills": recent_fills,
        "component_rows": component_rows,
    }
    return render(request, "dashboard/wallet_profile.html", context)


@login_required
def watchlist(request):
    """Target wallets (is_target=True) — quick score overview."""
    wallets = (
        Wallet.objects.filter(is_target=True, is_active=True)
        .annotate(
            latest_score=Max("scores__score_raw"),
            latest_deleveraged=Max("scores__score_deleveraged"),
        )
        .order_by("-latest_score")
    )
    context = {"wallets": wallets}
    return render(request, "dashboard/watchlist.html", context)


@login_required
def alerts_history(request):
    """Notification list with read/unread filter (PRD §18.1 Alerts)."""
    filter_read = request.GET.get("filter", "")
    notifications = Notification.objects.filter(user=request.user).select_related(
        "wallet", "alert_rule"
    )

    if filter_read == "unread":
        notifications = notifications.filter(read=False)
    elif filter_read == "read":
        notifications = notifications.filter(read=True)

    notifications = notifications.order_by("-created_at")[:200]

    context = {
        "notifications": notifications,
        "filter_read": filter_read,
    }
    return render(request, "dashboard/alerts_history.html", context)


@login_required
def settings_page(request):
    """View / edit WalletSettings thresholds (PRD §18.1 Settings).

    Accepts ?wallet=<address> to scope the form. Without a wallet selection
    we render the list of target wallets with their settings.
    """
    address = request.GET.get("wallet", "")
    wallet = None
    wallet_settings = None

    if address:
        wallet = get_object_or_404(Wallet, address=address.strip().lower())
        wallet_settings, _ = WalletSettings.objects.get_or_create(wallet=wallet)

        if request.method == "POST":
            promo = request.POST.get("promotion_threshold")
            demo = request.POST.get("demotion_threshold")
            consec = request.POST.get("demotion_consecutive_required")
            try:
                wallet_settings.promotion_threshold = int(promo)
                wallet_settings.demotion_threshold = int(demo)
                wallet_settings.demotion_consecutive_required = int(consec)
                wallet_settings.full_clean()
                wallet_settings.save()
                return redirect(f"/settings/?wallet={wallet.address}")
            except Exception:
                pass  # invalid input — fall through with current values

    target_wallets = list(
        Wallet.objects.filter(is_target=True, is_active=True).order_by("address")
    )
    # Pre-fetch settings rows into a dict keyed by wallet_id so the template
    # never triggers RelatedObjectDoesNotExist on wallets without settings.
    settings_by_wallet = {
        s.wallet_id: s
        for s in WalletSettings.objects.filter(
            wallet__is_target=True, wallet__is_active=True
        )
    }
    # Pair each wallet with its existing settings (or None) so the template
    # only iterates a simple list and never traverses the reverse relation.
    target_wallet_pairs = [
        (w, settings_by_wallet.get(w.id)) for w in target_wallets
    ]

    context = {
        "wallet": wallet,
        "wallet_settings": wallet_settings,
        "target_wallet_pairs": target_wallet_pairs,
    }
    return render(request, "dashboard/settings.html", context)

@login_required
def whale_copy_status(request):
    """
    Whale Copy status page (PRD §19) — shows live execution status,
    open positions, recent signals, and risk metrics.
    """
    from django.conf import settings as smr_settings
    from copytrading.models import SimulatedTrade

    live_enabled = smr_settings.HL_LIVE_EXECUTION

    # Get recent simulated trades (last 24h)
    from datetime import timedelta
    from django.utils import timezone
    now = timezone.now()
    day_ago = now - timedelta(hours=24)

    recent_trades = SimulatedTrade.objects.filter(
        opened_at__gte=day_ago
    ).select_related("profile", "wallet").order_by("-opened_at")[:20]

    # Summary stats
    total_trades = SimulatedTrade.objects.count()
    open_trades = SimulatedTrade.objects.filter(status="open").count()
    closed_trades = SimulatedTrade.objects.filter(status__in=["closed", "liquidated"]).count()

    context = {
        "live_enabled": live_enabled,
        "recent_trades": recent_trades,
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "risk_config": {
            "capital_per_trade": smr_settings.HL_CAPITAL_PER_TRADE_USD,
            "max_leverage": smr_settings.HL_MAX_LEVERAGE,
            "max_exposure_pct": smr_settings.HL_MAX_EXPOSURE_PCT,
            "max_open_positions": smr_settings.HL_MAX_OPEN_POSITIONS,
            "stop_loss_pct": smr_settings.HL_STOP_LOSS_PCT,
            "take_profit_pct": smr_settings.HL_TAKE_PROFIT_PCT,
        },
    }
    return render(request, "dashboard/whale_copy_status.html", context)


@login_required
def whale_copy_api_status(request):
    """
    JSON API endpoint for whale copy status — used by the dashboard
    real-time updates.
    """
    from django.conf import settings as smr_settings
    from django.http import JsonResponse
    from copytrading.models import SimulatedTrade

    live_enabled = smr_settings.HL_LIVE_EXECUTION

    total_trades = SimulatedTrade.objects.count()
    open_trades = SimulatedTrade.objects.filter(status="open").count()

    return JsonResponse({
        "live_enabled": live_enabled,
        "total_trades": total_trades,
        "open_trades": open_trades,
        "mode": "live" if live_enabled else "dry_run",
    })
