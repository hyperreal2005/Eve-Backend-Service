from collections.abc import Callable
from typing import Any

from django.db import connection
from django.http import HttpResponse
from django.test.utils import CaptureQueriesContext

PROBLEM_JSON = "application/problem+json"


def query_count(action: Callable[[], Any]) -> int:
    """How many SQL queries `action` runs (for N+1 regression tests)."""
    with CaptureQueriesContext(connection) as context:
        action()
    return len(context.captured_queries)


def assert_problem(response: HttpResponse, status: int, code: str) -> dict[str, Any]:
    """Assert an RFC 9457 error response and return its body."""
    assert response.status_code == status, response.content
    assert response["Content-Type"] == PROBLEM_JSON
    body = response.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"] == f"urn:eve-diagnostics:problem:{code.lower().replace('_', '-')}"
    assert body["request_id"] == response["X-Request-ID"]
    return body


def field_errors(body: dict[str, Any]) -> set[tuple[str | None, str]]:
    """The `(field, code)` pairs of a VALIDATION_ERROR body."""
    return {(error["field"], error["code"]) for error in body["errors"]}
