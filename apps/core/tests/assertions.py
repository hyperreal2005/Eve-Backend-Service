from typing import Any

from django.http import HttpResponse

PROBLEM_JSON = "application/problem+json"


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
