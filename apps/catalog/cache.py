"""Caching the public catalogue reads in Redis.

The catalogue is read far more often than it changes, and every patient sees the same thing, so
public GETs are answered from the cache. Administrators bypass it: they also see inactive records,
and must see their own changes at once.

Invalidation is by version, not by key. Responses are cached under the catalogue's current
version, and any write to a catalogue table replaces the version once its transaction commits:
every older entry is then unreachable, and expires on its own. Replacing it only after the commit
means no reader can cache pre-commit data under the new version. The TTL bounds staleness only if
a version change is lost (say Redis was briefly down when it was written).

The cache fails open (see ResilientRedisCache): without Redis, requests go to the database.
"""

import hashlib
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils.http import urlencode
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import is_admin

VERSION_KEY = "catalog:version"


def current_version() -> str | None:
    """The catalogue's cache version, or None if the cache can't be reached."""
    version = cache.get(VERSION_KEY)
    if version is None:
        # The first reader (or the first after eviction) sets one; `add` lets only one win.
        cache.add(VERSION_KEY, uuid.uuid4().hex, timeout=None)
        version = cache.get(VERSION_KEY)
    return version


def invalidate() -> None:
    """Retire every cached catalogue response by moving to a new version."""
    cache.set(VERSION_KEY, uuid.uuid4().hex, timeout=None)


def invalidate_on_commit(**_: Any) -> None:
    """Receiver for saves and deletes of catalogue rows.

    A signal rather than calls in the services, because the admin site edits catalogue rows too,
    without going through them. Bulk `QuerySet.update()` sends no signal; nothing uses it on the
    catalogue.
    """
    transaction.on_commit(invalidate, robust=True)


type ViewMethod = Callable[..., Response]


def cached_public_read(view_method: ViewMethod) -> ViewMethod:
    """Serve a catalogue GET from the cache, for everyone but administrators.

    Only 200 responses are stored; errors are raised before anything is cached. `X-Cache` says
    whether a response came from the cache (HIT) or was stored in it (MISS).
    """

    @wraps(view_method)
    def wrapper(view: APIView, request: Request, *args: Any, **kwargs: Any) -> Response:
        ttl = settings.CATALOG_CACHE_SECONDS
        version = current_version() if ttl and not is_admin(request.user) else None
        if version is None:
            return view_method(view, request, *args, **kwargs)

        key = _key(version, request)
        data = cache.get(key)
        if data is not None:
            return Response(data, headers={"X-Cache": "HIT"})
        response = view_method(view, request, *args, **kwargs)
        if response.status_code == 200:
            cache.set(key, response.data, ttl)
        response["X-Cache"] = "MISS"
        return response

    return wrapper


def _key(version: str, request: Request) -> str:
    # The host is part of it because pagination links in the body are absolute URLs. Sorting
    # the query makes ?a=1&b=2 and ?b=2&a=1 one entry.
    query = urlencode(sorted(request.query_params.lists()), doseq=True)
    url = f"{request.scheme}://{request.get_host()}{request.path}?{query}"
    return f"catalog:{version}:{hashlib.sha256(url.encode()).hexdigest()}"
