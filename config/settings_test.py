"""Test settings: the real configuration plus a few speed and isolation tweaks."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-django-secret-key-0123456789abcdefghijklmn")
os.environ.setdefault("JWT_SIGNING_KEY", "test-only-jwt-signing-key-0123456789abcdefghijklmnop")

from config.settings import *  # noqa: F403
from config.settings import STORAGES

# Argon2 is slow on purpose. Tests hash with MD5; one test pins the production hasher.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Throttle counters live in the cache. A per-process cache that every test clears keeps tests
# independent of each other and of a running Redis.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# The manifest storage and WhiteNoise's startup scan both expect `collectstatic` output, which
# tests don't produce; serve static files on demand instead.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
WHITENOISE_AUTOREFRESH = True
