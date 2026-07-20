from pathlib import Path
import dj_database_url
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="django-insecure-dev-only-change-in-production")
DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
        "DIRS": [BASE_DIR / "templates"],
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
}

# DatabaseScheduler reads this dict on startup and upserts PeriodicTask records.
# Changes here are applied on the next `celery beat` restart.
CELERY_BEAT_SCHEDULE = {
    "discovery-fetch-leaderboard-every-6h": {
        "task": "discovery.tasks.fetch_leaderboard",
        "schedule": 6 * 60 * 60,  # 6 hours in seconds
        "options": {"queue": "discovery"},
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

# TMT Bridge (born disabled)

TMT_BRIDGE_ENABLED = config("TMT_BRIDGE_ENABLED", default=False, cast=bool)
TMT_BRIDGE_TOKEN = config("TMT_BRIDGE_TOKEN", default="")

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
