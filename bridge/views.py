"""
TMT Bridge API — born disabled (TMT_BRIDGE_ENABLED=False by default).

PRD §20.2 / Sprint 9: the endpoint exists and is testable in isolation but
returns 503 with an explicit message while the bridge flag is off, so the
TMT side can never silently consume stale data. When enabled, it returns the
top-N wallets ranked by ``score_raw`` for the requested window plus a
snapshot of their currently-open positions. Every request is recorded in
``BridgeAccessLog`` for auditability.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.http import JsonResponse
from django.views import View

from wallets.models import Position, WalletScore, Window

from .models import BridgeAccessLog

# Hard ceiling on the ``limit`` query param to keep the payload bounded —
# the bridge is read-only and authenticated downstream, but a runaway limit
# should not turn the endpoint into a full dump of the wallet universe.
MAX_LIMIT = 100


def _client_ip(request) -> str:
    """Originating requester IP, honoring the X-Forwarded-For header."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _open_positions_by_wallet(wallet_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch currently-open positions for the given wallet ids."""
    if not wallet_ids:
        return {}
    positions: dict[int, list[dict]] = {}
    queryset = Position.objects.filter(
        wallet_id__in=wallet_ids, status=Position.Status.OPEN
    ).values(
        "wallet_id",
        "asset",
        "side",
        "size",
        "entry_price",
        "leverage",
        "unrealized_pnl",
    )
    for row in queryset:
        wid = row.pop("wallet_id")
        positions.setdefault(wid, []).append(row)
    return positions


class SmartMoneySignalView(View):
    """
    GET /api/bridge/v1/smart-money-signal/

    Query params:
        window   — PRD §15.2 window label (default ``7d``)
        min_score — minimum ``score_raw`` to include (default ``70``)
        limit    — maximum number of wallets to return, capped at 100
                   (default ``20``)
    """

    def get(self, request):
        if not getattr(settings, "TMT_BRIDGE_ENABLED", False):
            body = {
                "error": "Bridge disabled",
                "message": "Set TMT_BRIDGE_ENABLED=True to activate",
            }
            response = JsonResponse(body, status=503)
            self._log(request, 503, body)
            return response

        window = request.GET.get("window", "7d")
        if window not in Window.values:
            body = {
                "error": "invalid_window",
                "message": f"window must be one of {sorted(Window.values)}",
            }
            response = JsonResponse(body, status=400)
            self._log(request, 400, body)
            return response

        raw_min_score = request.GET.get("min_score", "70")
        try:
            min_score = Decimal(str(raw_min_score))
        except (InvalidOperation, ValueError):
            body = {
                "error": "invalid_min_score",
                "message": "min_score must be a numeric value between 0 and 100",
            }
            response = JsonResponse(body, status=400)
            self._log(request, 400, body)
            return response
        if not (Decimal("0") <= min_score <= Decimal("100")):
            body = {
                "error": "invalid_min_score",
                "message": "min_score must be between 0 and 100",
            }
            response = JsonResponse(body, status=400)
            self._log(request, 400, body)
            return response

        try:
            limit = int(request.GET.get("limit", "20"))
        except ValueError:
            body = {
                "error": "invalid_limit",
                "message": "limit must be a positive integer",
            }
            response = JsonResponse(body, status=400)
            self._log(request, 400, body)
            return response
        if limit <= 0:
            body = {
                "error": "invalid_limit",
                "message": "limit must be a positive integer",
            }
            response = JsonResponse(body, status=400)
            self._log(request, 400, body)
            return response
        limit = min(limit, MAX_LIMIT)

        scores = list(
            WalletScore.objects.filter(window=window, score_raw__gte=min_score)
            .select_related("wallet")
            .order_by("-score_raw")[:limit]
        )
        positions_by_wallet = _open_positions_by_wallet([s.wallet_id for s in scores])

        results = []
        for s in scores:
            wallet_positions = positions_by_wallet.get(s.wallet_id, [])
            results.append(
                {
                    "address": s.wallet.address,
                    "score": float(s.score_raw),
                    "classification": s.classification,
                    "last_position_summary": {
                        "open_count": len(wallet_positions),
                        "positions": wallet_positions,
                    },
                }
            )

        body = {
            "window": window,
            "min_score": float(min_score),
            "limit": limit,
            "count": len(results),
            "results": results,
        }
        response = JsonResponse(body, status=200)
        self._log(request, 200, body)
        return response

    def _log(self, request, response_code: int, data_snapshot: dict) -> None:
        """Persist one BridgeAccessLog row regardless of the response status."""
        BridgeAccessLog.objects.create(
            requester_ip=_client_ip(request) or None,
            endpoint=request.path,
            response_code=response_code,
            data_snapshot=data_snapshot,
        )