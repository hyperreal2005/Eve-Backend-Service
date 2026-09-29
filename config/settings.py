"""Settings for the EVE diagnostics booking service.

Everything environment-specific comes from environment variables (12-factor); a `.env` file is
read for local development only (see `.env.example`). Business policies are settings too, so
they can be tuned without code changes.
"""

from datetime import timedelta
from pathlib import Path

import environ

from apps.core.logging import build_logging_config

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
if (BASE_DIR / ".env").is_file():
    environ.Env.read_env(BASE_DIR / ".env")


# --------------------------------------------------------------------------------------- core

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.bookings",
    "apps.payments",
    "apps.mockpay",
]

MIDDLEWARE = [
    # Outermost, so the request id and the access log cover everything below, including errors.
    "apps.core.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.middleware.OptionalTrailingSlashMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Trailing slashes are optional (OptionalTrailingSlashMiddleware rewrites the path in place).
# Django's own APPEND_SLASH would answer with a 301, which breaks POST requests and webhooks.
APPEND_SLASH = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024  # 1 MiB: far above any legitimate request body


# ----------------------------------------------------------------------------------- database

DATABASES = {"default": env.db("DATABASE_URL", default="postgres://eve:eve@localhost:5433/eve")}
DATABASES["default"].update(
    CONN_MAX_AGE=env.int("DB_CONN_MAX_AGE", default=60),
    CONN_HEALTH_CHECKS=True,
)
DATABASES["default"].setdefault("OPTIONS", {}).update(
    connect_timeout=5,
    # Fail fast rather than queue forever: a request that can't get a row lock in time answers
    # 503 + Retry-After, which is safe because every write path is idempotent.
    options=(
        f"-c lock_timeout={env.int('DB_LOCK_TIMEOUT_MS', default=3000)} "
        f"-c statement_timeout={env.int('DB_STATEMENT_TIMEOUT_MS', default=10000)} "
        "-c idle_in_transaction_session_timeout=60000"
    ),
)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ------------------------------------------------------------------------------------- cache

# Backs rate limiting and the catalogue cache. It fails open: losing Redis degrades service
# instead of breaking it.
CACHES = {
    "default": {
        "BACKEND": "apps.core.cache.ResilientRedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6380/0"),
        "KEY_PREFIX": "eve",
        "OPTIONS": {"socket_connect_timeout": 0.5, "socket_timeout": 0.5},
    }
}
# Public catalogue reads are cached this long; any catalogue write retires them at once, so this
# only bounds staleness if that invalidation is lost. 0 turns the cache off.
CATALOG_CACHE_SECONDS = env.int("CATALOG_CACHE_SECONDS", default=300)


# -------------------------------------------------------------------------------- auth & users

AUTH_USER_MODEL = "accounts.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ("email", "full_name")},
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY"),
    "AUDIENCE": "eve-diagnostics-api",
    "ISSUER": "eve-diagnostics",
    "LEEWAY": 5,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "sub",
}


# -------------------------------------------------------------------------------------- API

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="120/min"),
        "user": env("THROTTLE_USER", default="600/min"),
        "auth_login": env("THROTTLE_AUTH_LOGIN", default="10/min"),
        "auth_signup": env("THROTTLE_AUTH_SIGNUP", default="20/hour"),
        "payments": env("THROTTLE_PAYMENTS", default="30/min"),
    },
    # 0 = trust only REMOTE_ADDR. Behind a load balancer set this to the number of proxies,
    # otherwise clients could spoof X-Forwarded-For to dodge rate limits.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    "EXCEPTION_HANDLER": "apps.core.problem_details.exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EVE Diagnostics API",
    "DESCRIPTION": (
        "Book diagnostic tests at partner centres and pay through a simulated provider. "
        "Every error is an RFC 9457 `application/problem+json` body with a stable `code`."
    ),
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "PREPROCESSING_HOOKS": ["apps.core.openapi.only_versioned_endpoints"],
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True, "displayRequestDuration": True},
    # Stable enum names in the schema, however many fields share a set of choices.
    "ENUM_NAME_OVERRIDES": {
        "BookingStatusEnum": "apps.bookings.models.BookingStatus",
        "BookingStatusReasonEnum": "apps.bookings.models.BookingStatusReason",
        "ActorTypeEnum": "apps.bookings.models.ActorType",
        "DiagnosticCategoryEnum": "apps.catalog.models.DiagnosticCategory",
        "PaymentStatusEnum": "apps.payments.models.PaymentStatus",
        "RefundStatusEnum": "apps.payments.models.RefundStatus",
    },
}


# --------------------------------------------------------------------------- business policy

BOOKING_MIN_LEAD = timedelta(minutes=env.int("BOOKING_MIN_LEAD_MINUTES", default=60))
BOOKING_MAX_ADVANCE = timedelta(days=env.int("BOOKING_MAX_ADVANCE_DAYS", default=30))
BOOKING_SLOT_MINUTES = env.int("BOOKING_SLOT_MINUTES", default=15)
# How long an unpaid booking holds its slot.
BOOKING_HOLD = timedelta(minutes=env.int("BOOKING_HOLD_MINUTES", default=15))
# Confirmed bookings can be cancelled until this long before the appointment.
BOOKING_CANCELLATION_CUTOFF = timedelta(
    hours=env.int("BOOKING_CANCELLATION_CUTOFF_HOURS", default=2)
)


# ---------------------------------------------------------------------------------- payments

# The provider adapter (a PaymentGateway). MockPay simulates one; production would plug in a
# real adapter here without touching bookings or payments.
PAYMENT_GATEWAY = env("PAYMENT_GATEWAY", default="apps.mockpay.gateway.MockPayGateway")
# Secrets accepted on incoming webhooks. Several may be listed so a secret can be rotated
# without downtime: sign with the new one while still accepting the old.
WEBHOOK_SECRETS = env.list("WEBHOOK_SECRETS")
# How old a webhook signature may be before it's treated as a replay.
WEBHOOK_TOLERANCE_SECONDS = env.int("WEBHOOK_TOLERANCE_SECONDS", default=300)

# MockPay, the simulated provider.
MOCKPAY_WEBHOOK_SECRET = env("MOCKPAY_WEBHOOK_SECRET", default=WEBHOOK_SECRETS[0])
MOCKPAY_WEBHOOK_URL = env(
    "MOCKPAY_WEBHOOK_URL", default="http://localhost:8000/api/v1/payments/webhook/"
)
# Chance that the default method (mock_random) succeeds.
MOCKPAY_SUCCESS_RATE = env.float("MOCKPAY_SUCCESS_RATE", default=0.8)
# Like a real provider, MockPay pushes results by webhook (needs a Celery worker): after a short
# delay, at least once, retrying with backoff, and sometimes twice on purpose.
MOCKPAY_WEBHOOKS_ENABLED = env.bool("MOCKPAY_WEBHOOKS_ENABLED", default=True)
MOCKPAY_WEBHOOK_DELAY_SECONDS = env.int("MOCKPAY_WEBHOOK_DELAY_SECONDS", default=2)
MOCKPAY_DUPLICATE_DELIVERY_RATE = env.float("MOCKPAY_DUPLICATE_DELIVERY_RATE", default=0.2)
MOCKPAY_DELIVERY_MAX_ATTEMPTS = env.int("MOCKPAY_DELIVERY_MAX_ATTEMPTS", default=8)
# How long an asynchronous (UPI-style) charge takes to settle.
MOCKPAY_ASYNC_SETTLE_SECONDS = env.int("MOCKPAY_ASYNC_SETTLE_SECONDS", default=10)

# Payments still PENDING after this long are checked with the provider by reconciliation.
PAYMENT_RECONCILE_AFTER = timedelta(seconds=env.int("PAYMENT_RECONCILE_AFTER_SECONDS", default=120))


# ------------------------------------------------------------------------------------ celery

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6380/1")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {
    # A task a worker took but never acknowledged is redelivered after this.
    "visibility_timeout": 3600,
    "socket_connect_timeout": 1,
}
# Publishing fails fast when the broker is down: callers log it and carry on, since no request
# depends on a task for correctness.
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    "max_retries": 2,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.5,
}
# At-least-once: acknowledge after the task finishes, so a crashed worker's task runs again.
# Safe because every task is idempotent.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TIME_LIMIT = 60
CELERY_TASK_SOFT_TIME_LIMIT = 45
CELERY_WORKER_HIJACK_ROOT_LOGGER = False  # keep the structured logging configured below
CELERY_BEAT_SCHEDULE = {
    "expire-stale-booking-holds": {"task": "bookings.expire_stale_holds", "schedule": 60.0},
    "reconcile-stale-payments": {"task": "payments.reconcile_stale_payments", "schedule": 60.0},
}


# -------------------------------------------------------------------------- static & locale

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------------- security

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if env.bool("DJANGO_BEHIND_TLS_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=False)
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=0)


# ----------------------------------------------------------------------------------- logging

LOGGING = build_logging_config(
    json_logs=env.bool("LOG_JSON", default=not DEBUG),
    level=env("LOG_LEVEL", default="INFO"),
)
