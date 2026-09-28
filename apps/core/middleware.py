"""Request-scoped middleware: request ids, access logging and optional trailing slashes."""

import re
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar

import structlog
from django.http import HttpRequest, HttpResponse
from django.urls import is_valid_path

log = structlog.get_logger("apps.http")

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{8,128}")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    """The id of the request being handled on this thread, if any."""
    return _request_id.get()


class RequestContextMiddleware:
    """Gives every request an id, binds it to every log line and echoes it in the response.

    A well-formed incoming `X-Request-ID` is kept, so one request can be traced across services.
    Anything else is replaced, which also stops log injection through that header.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]
        token = _request_id.set(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = self.get_response(request)
            response[REQUEST_ID_HEADER] = request_id
            self._log(request, response, started)
            return response
        finally:
            structlog.contextvars.clear_contextvars()
            _request_id.reset(token)

    @staticmethod
    def _log(request: HttpRequest, response: HttpResponse, started: float) -> None:
        emit = log.debug if request.path.startswith("/api/v1/health/") else log.info
        emit(
            "http.request",
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )


class OptionalTrailingSlashMiddleware:
    """Serves `/api/v1/bookings` exactly like `/api/v1/bookings/`, without a redirect.

    Django's APPEND_SLASH answers with a 301: most clients then repeat a POST as a GET and drop
    its body, and webhook senders count any 3xx as a failed delivery. Rewriting the path in place
    avoids both, so clients may include or omit the trailing slash.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path_info
        if not path.endswith("/"):
            urlconf = getattr(request, "urlconf", None)
            if not is_valid_path(path, urlconf) and is_valid_path(f"{path}/", urlconf):
                request.path_info = f"{path}/"
                request.path = f"{request.path}/"
        return self.get_response(request)
