import re

import pytest
from structlog.testing import capture_logs

from apps.core.tests.assertions import assert_problem

LIVE_URL = "/api/v1/health/live/"


def test_a_request_id_is_generated_and_echoed(api_client):
    response = api_client.get(LIVE_URL)
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_a_well_formed_incoming_request_id_is_kept(api_client):
    response = api_client.get(LIVE_URL, HTTP_X_REQUEST_ID="trace-abc-12345")
    assert response["X-Request-ID"] == "trace-abc-12345"


@pytest.mark.parametrize("incoming", ["short", "has spaces in it", "x" * 200, "evil\nlog-line"])
def test_a_malformed_incoming_request_id_is_replaced(api_client, incoming):
    response = api_client.get(LIVE_URL, HTTP_X_REQUEST_ID=incoming)
    assert response["X-Request-ID"] != incoming
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_every_request_is_logged_once_with_its_outcome(api_client, db):
    with capture_logs() as logs:
        response = api_client.post("/api/v1/auth/login/", {})
    [entry] = [entry for entry in logs if entry["event"] == "http.request"]
    assert entry["status"] == response.status_code == 400
    assert entry["method"] == "POST"
    assert entry["path"] == "/api/v1/auth/login/"


def test_trailing_slash_is_optional_and_never_redirects(api_client, db):
    without_slash = api_client.post("/api/v1/auth/login", {})
    with_slash = api_client.post("/api/v1/auth/login/", {})
    assert without_slash.status_code == with_slash.status_code == 400
    assert without_slash.json()["errors"] == with_slash.json()["errors"]


def test_unknown_path_without_slash_is_still_a_404(api_client):
    assert_problem(api_client.get("/api/v1/nothing-here"), 404, "NOT_FOUND")
