"""Health-check endpoint for the SMR stack (PRD Sprint 10 §Observability).

Public, unauthenticated JSON view that the orchestrator (Docker Swarm
healthcheck, Traefik, external probes) can poll to verify that the web
process can reach its dependencies (DB, Redis, Celery workers).

Response shape::

    {
      "status": "ok|degraded|down",
      "checks": {
        "db": "ok",
        "redis": "ok",
        "celery": "ok",
        "celery_workers": 4
      },
      "version": "<git sha or unknown>"
    }
"""

from __future__ import annotations

import os

from django.conf import settings
from django.http import JsonResponse


def _check_db() -> bool:
    try:
        from django.db import connections

        conn = connections["default"]
        conn.ensure_connection()
        return True
    except Exception:
        return False


def _check_redis() -> bool:
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        return bool(client.ping())
    except Exception:
        return False


def _celery_workers() -> int:
    try:
        from celery import current_app

        inspect = current_app.control.inspect(timeout=2)
        stats = inspect.stats() or {}
        return len(stats)
    except Exception:
        return 0


def health_check(request):
    """Aggregate health view — no auth, returns JSON."""

    db_ok = _check_db()
    redis_ok = _check_redis()
    worker_count = _celery_workers()
    celery_ok = worker_count > 0

    checks = {
        "db": "ok" if db_ok else "down",
        "redis": "ok" if redis_ok else "down",
        "celery": "ok" if celery_ok else "down",
        "celery_workers": worker_count,
    }

    if db_ok and redis_ok and celery_ok:
        status = "ok"
        http_status = 200
    elif db_ok or redis_ok or celery_ok:
        status = "degraded"
        http_status = 200
    else:
        status = "down"
        http_status = 503

    return JsonResponse(
        {
            "status": status,
            "checks": checks,
            "version": os.environ.get("SMR_VERSION", "unknown"),
        },
        status=http_status,
    )
