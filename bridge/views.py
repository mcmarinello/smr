"""
TMT Bridge API — born disabled (TMT_BRIDGE_ENABLED=False by default).
Returns 503 when disabled, as per PRD section 20.2.
"""

from django.http import JsonResponse
from django.conf import settings
from django.views import View


class SmartMoneySignalView(View):
    def get(self, request):
        if not getattr(settings, "TMT_BRIDGE_ENABLED", False):
            return JsonResponse(
                {
                    "error": "Bridge disabled",
                    "detail": "Set TMT_BRIDGE_ENABLED=True to activate this endpoint.",
                },
                status=503,
            )

        asset = request.GET.get("asset", "").upper()
        window = request.GET.get("window", "7d")

        if not asset:
            return JsonResponse({"error": "asset parameter is required"}, status=400)

        from wallets.models import Position, Wallet
        from django.db.models import Count

        target_wallets = Wallet.objects.filter(is_target=True, is_active=True)
        open_positions = Position.objects.filter(
            wallet__in=target_wallets, asset=asset, status="open"
        )

        count = open_positions.count()
        long_count = open_positions.filter(side="long").count()
        short_count = open_positions.filter(side="short").count()

        if count == 0:
            bias = "none"
        elif long_count > short_count:
            bias = "long"
        elif short_count > long_count:
            bias = "short"
        else:
            bias = "mixed"

        return JsonResponse(
            {
                "asset": asset,
                "window": window,
                "target_wallets_count": count,
                "net_position_bias": bias,
                "avg_score_of_participants": None,
                "convergence_detected_at": None,
            }
        )
