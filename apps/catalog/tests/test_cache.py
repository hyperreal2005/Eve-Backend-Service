"""The public catalogue read cache: versioned keys, retired on commit, bypassed by admins."""

import pytest
from django.core.cache import cache
from django.db import transaction

from apps.catalog.cache import VERSION_KEY
from apps.catalog.models import Offering
from apps.catalog.tests.factories import DiagnosticCentreFactory, OfferingFactory
from apps.core.cache import ResilientRedisCache
from apps.core.tests.assertions import query_count

pytestmark = pytest.mark.django_db

CENTRES_URL = "/api/v1/centres/"


@pytest.fixture(autouse=True)
def _cache_on(settings):
    settings.CATALOG_CACHE_SECONDS = 300


def offerings_url(centre_id) -> str:
    return f"{CENTRES_URL}{centre_id}/tests/"


def test_a_repeated_read_is_served_from_the_cache_without_touching_the_database(api_client):
    DiagnosticCentreFactory.create_batch(2)
    first = api_client.get(CENTRES_URL)
    assert first["X-Cache"] == "MISS"

    queries = query_count(lambda: api_client.get(CENTRES_URL))
    second = api_client.get(CENTRES_URL)

    assert queries == 0
    assert second["X-Cache"] == "HIT"
    assert second.json() == first.json()


def test_a_catalogue_change_retires_cached_reads_once_it_commits(
    api_client, admin_client, django_capture_on_commit_callbacks
):
    offering = OfferingFactory(price=100_000)
    url = offerings_url(offering.centre_id)
    api_client.get(url)

    with django_capture_on_commit_callbacks(execute=True):
        admin_client.patch(f"{url}{offering.test_id}/", {"price": 90_000})

    response = api_client.get(url)
    assert response["X-Cache"] == "MISS"
    assert response.json()["results"][0]["price"] == 90_000


def test_nothing_is_retired_before_the_write_commits(
    api_client, django_capture_on_commit_callbacks
):
    """A reader during the write must not cache old data under a version that outlives it."""
    offering = OfferingFactory()
    api_client.get(offerings_url(offering.centre_id))

    with django_capture_on_commit_callbacks(execute=False) as pending, transaction.atomic():
        offering.price = 1
        offering.save()

    assert len(pending) == 1  # the invalidation, waiting for the commit
    assert api_client.get(offerings_url(offering.centre_id))["X-Cache"] == "HIT"


def test_edits_outside_the_api_retire_cached_reads_too(
    api_client, django_capture_on_commit_callbacks
):
    """The admin site saves models directly, without the catalogue services."""
    offering = OfferingFactory()
    api_client.get(offerings_url(offering.centre_id))

    with django_capture_on_commit_callbacks(execute=True):
        Offering.objects.get(pk=offering.pk).delete()

    response = api_client.get(offerings_url(offering.centre_id))
    assert (response["X-Cache"], response.json()["count"]) == ("MISS", 0)


def test_administrators_bypass_the_cache_both_ways(api_client, admin_client):
    centre = DiagnosticCentreFactory()
    DiagnosticCentreFactory(is_active=False)
    assert api_client.get(CENTRES_URL).json()["count"] == 1  # cached for the public

    response = admin_client.get(CENTRES_URL)

    assert "X-Cache" not in response
    assert response.json()["count"] == 2  # not the public copy
    assert api_client.get(CENTRES_URL).json()["results"][0]["id"] == str(centre.id)


def test_query_order_does_not_matter_but_the_query_and_host_do(api_client):
    DiagnosticCentreFactory()
    api_client.get(f"{CENTRES_URL}?city=Gurugram&ordering=name")

    assert api_client.get(f"{CENTRES_URL}?ordering=name&city=Gurugram")["X-Cache"] == "HIT"
    assert api_client.get(f"{CENTRES_URL}?city=Pune&ordering=name")["X-Cache"] == "MISS"
    other_host = api_client.get(f"{CENTRES_URL}?city=Gurugram&ordering=name", HTTP_HOST="localhost")
    assert other_host["X-Cache"] == "MISS"


def test_errors_are_not_cached(api_client):
    response = api_client.get(f"{CENTRES_URL}00000000-0000-0000-0000-000000000000/")
    assert response.status_code == 404
    assert "X-Cache" not in response


def test_the_cache_can_be_switched_off(api_client, settings):
    settings.CATALOG_CACHE_SECONDS = 0
    assert "X-Cache" not in api_client.get(CENTRES_URL)


def test_without_redis_reads_go_to_the_database(api_client, settings):
    ResilientRedisCache._last_warning_at = float("-inf")
    settings.CACHES = {
        "default": {
            "BACKEND": "apps.core.cache.ResilientRedisCache",
            "LOCATION": "redis://127.0.0.1:1/0",  # nothing listens here
            "OPTIONS": {"socket_connect_timeout": 0.05, "socket_timeout": 0.05},
        }
    }
    DiagnosticCentreFactory()

    response = api_client.get(CENTRES_URL)

    assert (response.status_code, response.json()["count"]) == (200, 1)
    assert "X-Cache" not in response


def test_a_lost_version_is_recreated_and_old_entries_are_never_read(api_client):
    DiagnosticCentreFactory()
    api_client.get(CENTRES_URL)
    cache.delete(VERSION_KEY)  # evicted

    assert api_client.get(CENTRES_URL)["X-Cache"] == "MISS"
    assert api_client.get(CENTRES_URL)["X-Cache"] == "HIT"
