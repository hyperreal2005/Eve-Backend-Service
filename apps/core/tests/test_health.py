from apps.core import views

LIVE_URL = "/api/v1/health/live/"
READY_URL = "/api/v1/health/ready/"


def test_liveness_needs_no_dependencies(api_client):
    response = api_client.get(LIVE_URL)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_each_dependency(api_client, db):
    response = api_client.get(READY_URL)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "cache": "ok"}}


def test_readiness_fails_without_the_database(api_client, monkeypatch):
    monkeypatch.setattr(views, "_check_database", lambda: "error")
    response = api_client.get(READY_URL)
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_a_missing_cache_degrades_but_does_not_fail_readiness(api_client, db, monkeypatch):
    monkeypatch.setattr(views, "_check_cache", lambda: "degraded")
    response = api_client.get(READY_URL)
    assert response.status_code == 200
    assert response.json()["checks"]["cache"] == "degraded"
