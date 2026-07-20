"""
Hyperliquid Info API REST client.

All public endpoints — no authentication required.
Every request goes through the central rate limiter.
Retries use exponential backoff via tenacity.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
from django.conf import settings

from .rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

# Weight of each endpoint (conservative; adjust if HL publishes exact weights)
_WEIGHTS: dict[str, int] = {
    "userFills": 2,
    "clearinghouseState": 2,
    "meta": 1,
    "allMids": 1,
    "l2Book": 2,
    "candleSnapshot": 3,
    "leaderboard": 2,
}


def _weight_for(request_type: str) -> int:
    return _WEIGHTS.get(request_type, 1)


class HyperliquidClient:
    """
    Thin wrapper over the Hyperliquid Info API.
    One instance per worker is fine; httpx.Client is thread-safe.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or getattr(
            settings, "HYPERLIQUID_INFO_API_URL", "https://api.hyperliquid.xyz/info"
        )
        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "HyperliquidClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _post(self, payload: dict[str, Any], request_type: str) -> Any:
        weight = _weight_for(request_type)
        get_rate_limiter().acquire_or_raise(weight)
        logger.debug("HL API -> %s (weight=%d)", request_type, weight)
        response = self._http.post("", json=payload)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------ #
    # Public endpoints                                                     #
    # ------------------------------------------------------------------ #

    def user_fills(self, address: str) -> list[dict]:
        """Returns up to 2000 most-recent fills for the given wallet address."""
        return self._post({"type": "userFills", "user": address}, "userFills")

    def user_fills_by_time(
        self, address: str, start_time: int, end_time: int | None = None
    ) -> list[dict]:
        payload: dict[str, Any] = {
            "type": "userFillsByTime",
            "user": address,
            "startTime": start_time,
        }
        if end_time is not None:
            payload["endTime"] = end_time
        return self._post(payload, "userFills")

    def clearinghouse_state(self, address: str) -> dict:
        """Returns current open positions and account state."""
        return self._post({"type": "clearinghouseState", "user": address}, "clearinghouseState")

    def meta(self) -> dict:
        """Returns exchange metadata (assets, limits, etc.)."""
        return self._post({"type": "meta"}, "meta")

    def all_mids(self) -> dict[str, str]:
        """Returns current mid prices for all assets."""
        return self._post({"type": "allMids"}, "allMids")

    def l2_book(self, coin: str, n_sig_figs: int | None = None) -> dict:
        payload: dict[str, Any] = {"type": "l2Book", "coin": coin}
        if n_sig_figs is not None:
            payload["nSigFigs"] = n_sig_figs
        return self._post(payload, "l2Book")

    def candle_snapshot(
        self,
        coin: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
    ) -> list[dict]:
        payload: dict[str, Any] = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_time},
        }
        if end_time is not None:
            payload["req"]["endTime"] = end_time
        return self._post(payload, "candleSnapshot")

    def leaderboard(self) -> dict:
        return self._post({"type": "leaderboard"}, "leaderboard")
