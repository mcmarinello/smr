from pathlib import Path
import dj_database_url
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="django-insecure-dev-only-change-in-production")
DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*", cast=Csv())
CSRF_TRUSTED_ORIGINS = ["https://kagetrade.com", "https://*.kagetrade.com"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_celery_beat",
    "django_celery_results",
    "django_extensions",
    # Project apps
    "accounts",
    "hyperliquid_client",
    "wallets",
    "discovery",
    "tracking",
    "alerts",
    "copytrading",
    "bridge",
    "dashboard",
    "wallet_engine",
    "monitoring",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "smr.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",
            BASE_DIR / "dashboard" / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "smr.wsgi.application"

# Database — TimescaleDB extension is enabled via migration

DATABASES = {
    "default": dj_database_url.config(
        default=config(
            "DATABASE_URL",
            default="postgres://smr:smrpass@localhost:5432/smr",
        ),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Redis / Cache

REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# Celery

CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = config(
    "CELERY_RESULT_BACKEND", default="redis://localhost:6379/1"
)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_ROUTES = {
    "discovery.*": {"queue": "discovery"},
    "tracking.*": {"queue": "tracking"},
    "wallets.tasks.*": {"queue": "scoring"},
    "alerts.*": {"queue": "alerts"},
    "copytrading.*": {"queue": "copytrading"},
}

# DatabaseScheduler reads this dict on startup and upserts PeriodicTask records.
# Changes here are applied on the next `celery beat` restart.
CELERY_BEAT_SCHEDULE = {
    "discovery-fetch-leaderboard-every-6h": {
        "task": "discovery.tasks.fetch_leaderboard",
        "schedule": 6 * 60 * 60,  # 6 hours in seconds
        "options": {"queue": "discovery"},
    },
    "scoring-compute-all-every-1h": {
        "task": "wallets.tasks.compute_all_scores",
        "schedule": 60 * 60,  # 1 hour in seconds
        "options": {"queue": "scoring"},
        "kwargs": {"target_only": False},
    },
    "scoring-refresh-ranks-every-15m": {
        "task": "wallets.tasks.refresh_ranks",
        "schedule": 15 * 60,  # 15 minutes in seconds
        "options": {"queue": "scoring"},
    },
    # PRD §16.1 — tracking pipeline fans out per wallet on the 'tracking' queue.
    "tracking-all-targets-every-5m": {
        "task": "tracking.tasks.track_all_targets",
        "schedule": 5 * 60,  # 5 minutes in seconds
        "options": {"queue": "tracking"},
    },
    # PRD §16.2 — convergence scan over recent opens on the 'tracking' queue.
    "tracking-detect-convergence-every-10m": {
        "task": "tracking.tasks.detect_convergence",
        "schedule": 10 * 60,  # 10 minutes in seconds
        "options": {"queue": "tracking"},
    },
    # PRD §17 — drain unsent Notifications on the 'alerts' queue. V1 just
    # logs the dispatch; the Telegram integration is the Sprint 6 stretch goal.
    "alerts-send-pending-notifications-every-1m": {
        "task": "alerts.tasks.send_pending_notifications",
        "schedule": 60,  # 1 minute in seconds
        "options": {"queue": "alerts"},
    },
    # PRD §Sprint 8 — Copy Trading (paper). Every 15m fan out one
    # run_copy_simulation task per active profile on the 'copytrading' queue.
    "copytrading-run-all-every-15m": {
        "task": "copytrading.tasks.run_all_copy_simulations",
        "schedule": 15 * 60,  # 15 minutes in seconds
        "options": {"queue": "copytrading"},
    },
    # PRD §Sprint 8 — time-stop stale open trades once an hour per profile.
    "copytrading-auto-close-stale-every-1h": {
        "task": "copytrading.tasks.auto_close_stale_all",
        "schedule": 60 * 60,  # 1 hour in seconds
        "options": {"queue": "copytrading"},
    },
}

# Discovery Engine tuning (override in .env)
DISCOVERY_MAX_STREAM_COINS = config("DISCOVERY_MAX_STREAM_COINS", default=20, cast=int)
DISCOVERY_STREAM_DURATION_SECS = config("DISCOVERY_STREAM_DURATION_SECS", default=300, cast=int)

# Internationalization

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Hyperliquid

HYPERLIQUID_INFO_API_URL = config(
    "HYPERLIQUID_INFO_API_URL", default="https://api.hyperliquid.xyz/info"
)
HYPERLIQUID_WS_URL = config(
    "HYPERLIQUID_WS_URL", default="wss://api.hyperliquid.xyz/ws"
)
HYPERLIQUID_RATE_LIMIT_WEIGHT_PER_MIN = config(
    "HYPERLIQUID_RATE_LIMIT_WEIGHT_PER_MIN", default=1200, cast=int
)

# Telegram

TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_DEFAULT_CHAT_ID = config("TELEGRAM_DEFAULT_CHAT_ID", default="")

# Alerts — PRD §17. Default cooldown for dedup: same (rule, wallet, event, asset)
# only fires once per hour. Override via .env for tighter/looser windows.
ALERT_DEDUP_COOLDOWN_SECONDS = config(
    "ALERT_DEDUP_COOLDOWN_SECONDS", default=3600, cast=int
)

# TMT Bridge (born disabled)

TMT_BRIDGE_ENABLED = config("TMT_BRIDGE_ENABLED", default=False, cast=bool)
TMT_BRIDGE_TOKEN = config("TMT_BRIDGE_TOKEN", default="")

# Whale Copy — Live Execution (born disabled)
# PRD §19: live execution is gated by explicit flag. When False (default),
# all copy trading runs in dry-run/paper mode. Only set to True when:
# 1. Paper results are satisfactory
# 2. HL_PRIVATE_KEY is configured
# 3. Admin explicitly enables it
HL_LIVE_EXECUTION = config("HL_LIVE_EXECUTION", default=False, cast=bool)
HL_PRIVATE_KEY = config("HL_PRIVATE_KEY", default="")

# Whale Copy — Risk Parameters
HL_CAPITAL_PER_TRADE_USD = config("HL_CAPITAL_PER_TRADE_USD", default=50.0, cast=float)
HL_MAX_LEVERAGE = config("HL_MAX_LEVERAGE", default=5, cast=int)
HL_MAX_EXPOSURE_PCT = config("HL_MAX_EXPOSURE_PCT", default=25.0, cast=float)
HL_MAX_OPEN_POSITIONS = config("HL_MAX_OPEN_POSITIONS", default=5, cast=int)
HL_STOP_LOSS_PCT = config("HL_STOP_LOSS_PCT", default=5.0, cast=float)
HL_TAKE_PROFIT_PCT = config("HL_TAKE_PROFIT_PCT", default=15.0, cast=float)
HL_MIN_SCORE_TO_COPY = config("HL_MIN_SCORE_TO_COPY", default=55, cast=int)
HL_SLIPPAGE_TOLERANCE = config("HL_SLIPPAGE_TOLERANCE", default=0.005, cast=float)

# Whale Copy — Monitor Settings
HL_POLL_INTERVAL_SEC = config("HL_POLL_INTERVAL_SEC", default=10, cast=int)
HL_WS_RECONNECT_SEC = config("HL_WS_RECONNECT_SEC", default=5, cast=int)
HL_EXECUTION_DELAY_SEC = config("HL_EXECUTION_DELAY_SEC", default=0.5, cast=float)
HL_COPY_MODE = config("HL_COPY_MODE", default="open_close")

# Logging

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "format": "[{levelname}] {asctime} {name} wallet={wallet} {message}",
            "style": "{",
            "defaults": {"wallet": "-"},
        },
        "simple": {
            "format": "[{levelname}] {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "hyperliquid_client": {"level": "DEBUG", "propagate": True},
        "wallets": {"level": "DEBUG", "propagate": True},
        "discovery": {"level": "DEBUG", "propagate": True},
        "tracking": {"level": "DEBUG", "propagate": True},
        "alerts": {"level": "DEBUG", "propagate": True},
    },
}

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

