"""A URLconf used only by tests: the real routes plus views that fail on purpose."""

from django.db import OperationalError
from django.http import HttpRequest, HttpResponse
from django.urls import include, path
from psycopg import errors as pg_errors
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.views import APIView


class _PublicView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)


class CrashingApi(_PublicView):
    def get(self, request: Request) -> None:
        raise RuntimeError("internal detail that must never reach the client")


class LockTimeoutApi(_PublicView):
    def get(self, request: Request) -> None:
        cause = pg_errors.LockNotAvailable("canceling statement due to lock timeout")
        raise OperationalError(str(cause)) from cause


def crashing_django_view(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("crash outside DRF")


urlpatterns = [
    path("test/crash/", CrashingApi.as_view()),
    path("test/lock-timeout/", LockTimeoutApi.as_view()),
    path("test/django-crash/", crashing_django_view),
    path("", include("config.urls")),
]

handler404 = "apps.core.problem_details.page_not_found"
handler500 = "apps.core.problem_details.server_error"
