"""Test settings: the real configuration plus a few speed and isolation tweaks."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-django-secret-key-0123456789abcdefghijklmn")
os.environ.setdefault("JWT_SIGNING_KEY", "test-only-jwt-signing-key-0123456789abcdefghijklmnop")
os.environ.setdefault("WEBHOOK_SECRETS", "whsec_dGVzdC1vbmx5LW1vY2twYXktd2ViaG9vay1zZWNyZXQ=")

from config.settings import *  # noqa: F403
from config.settings import STORAGES

# Argon2 is slow on purpose. Tests hash with MD5; one test pins the production hasher.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Throttle counters live in the cache. A per-process cache that every test clears keeps tests
# independent of each other and of a running Redis.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
# Factories write catalogue rows directly; a response cache would hide them from later requests.
# The cache's own tests switch it on.
CATALOG_CACHE_SECONDS = 0

# The manifest storage and WhiteNoise's startup scan both expect `collectstatic` output, which
# tests don't produce; serve static files on demand instead.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
WHITENOISE_AUTOREFRESH = True

# No broker in tests: MockPay doesn't push webhooks on its own, and tests that exercise
# background work run tasks eagerly (see the `eager_celery` fixture).
CELERY_BROKER_URL = "memory://"
MOCKPAY_WEBHOOKS_ENABLED = False
