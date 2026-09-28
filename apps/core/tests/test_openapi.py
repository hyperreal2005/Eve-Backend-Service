from django.core.management import call_command


def test_the_schema_generates_without_warnings(tmp_path):
    # --fail-on-warn turns any unresolvable serializer or view into a test failure.
    call_command("spectacular", "--fail-on-warn", "--file", str(tmp_path / "schema.yaml"))


def test_only_versioned_routes_are_documented(api_client):
    paths = api_client.get("/api/v1/schema/", HTTP_ACCEPT="application/json").json()["paths"]
    assert paths
    assert all(path.startswith("/api/v1/") for path in paths)


def test_swagger_ui_is_served(api_client):
    assert api_client.get("/api/docs/").status_code == 200
