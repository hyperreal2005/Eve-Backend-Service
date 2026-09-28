"""RFC 9457 problem details for every error response.

One exception handler turns domain errors, DRF errors and unexpected exceptions into
`application/problem+json` bodies with a stable machine-readable `code` and the request id, so
clients never parse HTML or guess at shapes. Django-level handlers cover requests that never
reach DRF (unknown URLs, crashes outside views).
"""

from collections.abc import Iterator, Mapping
from typing import Any

import structlog
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import DatabaseError
from django.http import Http404, HttpRequest, JsonResponse
from rest_framework import exceptions as drf
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework_simplejwt.exceptions import InvalidToken

from apps.core.db import is_transient_db_error
from apps.core.errors import DomainError
from apps.core.middleware import current_request_id

log = structlog.get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
AUTH_CHALLENGE = DomainError.www_authenticate

# DRF exception → (code, title). Validation, throttling and token errors get richer bodies below.
_DRF_PROBLEMS: tuple[tuple[type[drf.APIException], str, str], ...] = (
    (drf.AuthenticationFailed, "AUTHENTICATION_FAILED", "Authentication failed"),
    (drf.NotAuthenticated, "AUTHENTICATION_REQUIRED", "Authentication required"),
    (drf.PermissionDenied, "PERMISSION_DENIED", "Permission denied"),
    (drf.NotFound, "NOT_FOUND", "Not found"),
    (drf.MethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed"),
    (drf.NotAcceptable, "NOT_ACCEPTABLE", "Not acceptable"),
    (drf.UnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE", "Unsupported media type"),
    (drf.ParseError, "MALFORMED_REQUEST", "Malformed request"),
    (drf.Throttled, "RATE_LIMITED", "Too many requests"),
)


def problem_type(code: str) -> str:
    """A stable URI identifying the problem type (RFC 9457 §3.1.1)."""
    return f"urn:eve-diagnostics:problem:{code.lower().replace('_', '-')}"


def problem_body(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": problem_type(code),
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code,
        "request_id": current_request_id(),
    }
    # Extension members add detail but can never overwrite the standard ones.
    for key, value in (extensions or {}).items():
        body.setdefault(key, value)
    return body


def exception_handler(exc: Exception, context: Mapping[str, Any]) -> Response:
    """DRF `EXCEPTION_HANDLER`: every error leaves the API as problem+json."""
    request = context.get("request")
    instance = request.path if request is not None else ""

    if isinstance(exc, DomainError):
        body = problem_body(
            status=exc.status_code,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            instance=instance,
            extensions=exc.extra,
        )
        headers = {"WWW-Authenticate": exc.www_authenticate} if exc.status_code == 401 else None
        return _problem_response(body, headers=headers)

    if is_transient_db_error(exc):
        log.warning("db.transient_error", error=type(exc.__cause__).__name__)
        body = problem_body(
            status=503,
            code="SERVICE_UNAVAILABLE",
            title="Service temporarily unavailable",
            detail="The request collided with concurrent work. It is safe to retry.",
            instance=instance,
        )
        return _problem_response(body, headers={"Retry-After": "1"})

    if isinstance(exc, Http404):
        exc = drf.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf.PermissionDenied()

    response = drf_exception_handler(exc, context)
    if response is None:  # not an APIException: a bug or an infrastructure failure
        if isinstance(exc, DatabaseError):
            log.error("db.error", exc_info=exc)
        else:
            log.error("unhandled_exception", exc_info=exc)
        body = problem_body(
            status=500,
            code="INTERNAL_ERROR",
            title="Internal server error",
            detail="An unexpected error occurred. It has been logged.",
            instance=instance,
        )
        return _problem_response(body)

    response.data = _drf_problem(exc, response.status_code, instance)
    response.content_type = PROBLEM_CONTENT_TYPE
    if response.status_code == 401 and "WWW-Authenticate" not in response:
        response["WWW-Authenticate"] = AUTH_CHALLENGE
    return response


def page_not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    """Django `handler404`: unknown URLs (including malformed ids in paths)."""
    return _json_problem(request, 404, "NOT_FOUND", "Not found", "No resource matches this URL.")


def server_error(request: HttpRequest) -> JsonResponse:
    """Django `handler500`: a crash outside DRF's exception handling."""
    return _json_problem(
        request, 500, "INTERNAL_ERROR", "Internal server error", "An unexpected error occurred."
    )


def _drf_problem(exc: Exception, status: int, instance: str) -> dict[str, Any]:
    if isinstance(exc, drf.ValidationError):
        return problem_body(
            status=status,
            code="VALIDATION_ERROR",
            title="Invalid request",
            detail="One or more fields are invalid.",
            instance=instance,
            extensions={"errors": list(_flatten_errors(exc.detail))},
        )
    if isinstance(exc, drf.Throttled):
        return problem_body(
            status=status,
            code="RATE_LIMITED",
            title="Too many requests",
            detail="Request rate limit exceeded.",
            instance=instance,
            extensions={"retry_after": exc.wait},
        )
    if isinstance(exc, InvalidToken):
        return problem_body(
            status=status,
            code="TOKEN_INVALID",
            title="Invalid token",
            detail="The token is invalid, expired or revoked.",
            instance=instance,
        )
    code, title = _code_and_title(exc)
    detail = getattr(exc, "detail", "")
    return problem_body(
        status=status, code=code, title=title, detail=str(detail), instance=instance
    )


def _code_and_title(exc: Exception) -> tuple[str, str]:
    for exc_type, code, title in _DRF_PROBLEMS:
        if isinstance(exc, exc_type):
            return code, title
    return str(getattr(exc, "default_code", "error")).upper(), "Request failed"


def _flatten_errors(detail: Any, path: str = "") -> Iterator[dict[str, Any]]:
    """DRF's nested error structure → a flat list of `{field, code, message}`."""
    if isinstance(detail, Mapping):
        for key, value in detail.items():
            name = "" if key == api_settings.NON_FIELD_ERRORS_KEY else str(key)
            yield from _flatten_errors(value, f"{path}.{name}" if path and name else path or name)
    elif isinstance(detail, list):
        for index, item in enumerate(detail):
            if isinstance(item, Mapping | list):
                yield from _flatten_errors(item, f"{path}[{index}]")
            else:
                yield {
                    "field": path or None,
                    "code": getattr(item, "code", "invalid"),
                    "message": str(item),
                }
    else:
        yield {
            "field": path or None,
            "code": getattr(detail, "code", "invalid"),
            "message": str(detail),
        }


def _problem_response(body: dict[str, Any], headers: Mapping[str, str] | None = None) -> Response:
    return Response(body, status=body["status"], headers=headers, content_type=PROBLEM_CONTENT_TYPE)


def _json_problem(
    request: HttpRequest, status: int, code: str, title: str, detail: str
) -> JsonResponse:
    body = problem_body(status=status, code=code, title=title, detail=detail, instance=request.path)
    return JsonResponse(body, status=status, content_type=PROBLEM_CONTENT_TYPE)
