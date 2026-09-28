import pytest
from django.test import Client
from structlog.testing import capture_logs

from apps.core.problem_details import problem_body
from apps.core.tests.assertions import assert_problem, field_errors

LOGIN_URL = "/api/v1/auth/login/"
SIGNUP_URL = "/api/v1/auth/signup/"


def test_unknown_api_url_is_a_problem_json_404(api_client):
    body = assert_problem(api_client.get("/api/v1/no-such-thing/"), 404, "NOT_FOUND")
    assert body["instance"] == "/api/v1/no-such-thing/"


def test_unknown_url_outside_the_api_is_also_problem_json(api_client):
    assert_problem(api_client.get("/definitely/not/here"), 404, "NOT_FOUND")


def test_malformed_json_is_a_400(api_client):
    response = api_client.post(LOGIN_URL, data="{not json", content_type="application/json")
    assert_problem(response, 400, "MALFORMED_REQUEST")


def test_non_json_body_is_a_415(api_client):
    response = api_client.post(LOGIN_URL, data={"email": "a@b.co"}, format="multipart")
    assert_problem(response, 415, "UNSUPPORTED_MEDIA_TYPE")


def test_wrong_method_is_a_405(api_client):
    assert_problem(api_client.get(LOGIN_URL), 405, "METHOD_NOT_ALLOWED")


def test_validation_errors_are_a_flat_list_of_field_errors(api_client, db):
    response = api_client.post(SIGNUP_URL, {"email": "not-an-email", "password": "short"})
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert {
        ("email", "invalid"),
        ("password", "min_length"),
        ("full_name", "required"),
    } <= field_errors(body)


@pytest.mark.urls("apps.core.tests.urls")
def test_unhandled_exception_is_a_500_without_internal_details(api_client):
    with capture_logs() as logs:
        response = api_client.get("/test/crash/")
    body = assert_problem(response, 500, "INTERNAL_ERROR")
    assert "internal detail" not in response.content.decode()
    assert body["detail"] == "An unexpected error occurred. It has been logged."
    assert any(entry["event"] == "unhandled_exception" for entry in logs)


@pytest.mark.urls("apps.core.tests.urls")
def test_transient_database_error_is_a_retryable_503(api_client):
    response = api_client.get("/test/lock-timeout/")
    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert response["Retry-After"] == "1"


def test_extension_members_never_overwrite_standard_members():
    body = problem_body(
        status=409,
        code="CONFLICT",
        title="Conflict",
        detail="d",
        instance="/x",
        extensions={"status": "PENDING", "booking_id": "b-1"},
    )
    assert (body["status"], body["booking_id"]) == (409, "b-1")


@pytest.mark.urls("apps.core.tests.urls")
def test_crash_outside_drf_is_still_problem_json():
    client = Client(raise_request_exception=False)
    assert_problem(client.get("/test/django-crash/"), 500, "INTERNAL_ERROR")
