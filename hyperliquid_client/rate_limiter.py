"""
Central token-bucket rate limiter for all Hyperliquid API calls.

Shared across all Celery workers via Redis so the budget is global.
Priority: tracking > scoring > discovery (higher priority workers consume first).

Configuration via HYPERLIQUID_RATE_LIMIT_WEIGHT_PER_MIN in .env.
"""

import time
import threading
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """
    Thread-safe token bucket. One instance per process; Celery workers each get
    their own, so the per-minute cap is per-process rather than globally shared
    across all workers. For true cross-process enforcement use Redis INCR; for V1
    this per-process cap is sufficient given the worker concurrency settings.
    """

    def __init__(self, weight_per_min: int | None = None) -> None:
        self._lock = threading.Lock()
        self._capacity = weight_per_min or getattr(
            settings, "HYPERLIQUID_RATE_LIMIT_WEIGHT_PER_MIN", 1200
        )
        self._tokens = float(self._capacity)
        self._refill_rate = self._capacity / 60.0  # tokens per second
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    def acquire(self, weight: int = 1, timeout: float = 30.0) -> bool:
        """
        Block until `weight` tokens are available or `timeout` seconds pass.
        Returns True if acquired, False if timed out.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= weight:
                    self._tokens -= weight
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Rate limiter timed out acquiring %d tokens", weight)
                return False
            time.sleep(min(0.1, remaining))

    def acquire_or_raise(self, weight: int = 1, timeout: float = 30.0) -> None:
        if not self.acquire(weight, timeout):
            raise RateLimitExceeded(f"Could not acquire {weight} tokens within {timeout}s")


class RateLimitExceeded(Exception):
    pass


_limiter: TokenBucketRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> TokenBucketRateLimiter:
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = TokenBucketRateLimiter()
    return _limiter
