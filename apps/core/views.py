import uuid

from django.core.cache import cache
from django.db import DatabaseError, connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class _PublicProbe(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = ()


class LivenessApi(_PublicProbe):
    @extend_schema(
        tags=["health"],
        auth=[],
        responses=inline_serializer("Liveness", {"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        """The process is up and serving requests."""
        return Response({"status": "ok"})


class ReadinessApi(_PublicProbe):
    @extend_schema(
        tags=["health"],
        auth=[],
        responses=inline_serializer(
            "Readiness", {"status": serializers.CharField(), "checks": serializers.DictField()}
        ),
    )
    def get(self, request: Request) -> Response:
        """Dependencies are reachable: the database is required, the cache is optional."""
        checks = {"database": _check_database(), "cache": _check_cache()}
        ready = checks["database"] == "ok"
        return Response(
            {"status": "ok" if ready else "unavailable", "checks": checks},
            status=200 if ready else 503,
        )


def _check_database() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return "error"
    return "ok"


def _check_cache() -> str:
    probe = uuid.uuid4().hex
    cache.set("health:probe", probe, timeout=5)
    return "ok" if cache.get("health:probe") == probe else "degraded"
