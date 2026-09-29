"""Retrying POST /bookings/ with an Idempotency-Key."""

from datetime import UTC, timedelta

import pytest
import time_machine
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory, bearer
from apps.bookings.models import Booking, BookingStatus
from apps.bookings.tests.factories import future_slot
from apps.catalog.tests.factories import OfferingFactory
from apps.core.tests.assertions import assert_problem, field_errors

pytestmark = pytest.mark.django_db

BOOKINGS_URL = "/api/v1/bookings/"
KEY = "9d5c1a4e-6f3b-4b8a-a2d7-0c1e5f9b3a6d"


def post(client, offering, key=KEY, **overrides):
    payload = {
        "centre_id": str(offering.centre_id),
        "test_id": str(offering.test_id),
        "appointment_at": future_slot().isoformat(),
        **overrides,
    }
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    return client.post(BOOKINGS_URL, payload, **headers)


@pytest.fixture
def offering():
    return OfferingFactory()


def test_a_retry_with_the_same_key_returns_the_original_booking(user_client, offering):
    first = post(user_client, offering)
    retry = post(user_client, offering)

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json()["id"] == first.json()["id"]
    assert Booking.objects.count() == 1


def test_without_a_key_the_same_retry_is_a_duplicate(user_client, offering):
    post(user_client, offering, key=None)
    assert_problem(post(user_client, offering, key=None), 409, "DUPLICATE_BOOKING")


def test_a_retry_gets_the_booking_as_it_is_now(user_client, offering):
    booking_id = post(user_client, offering).json()["id"]
    Booking.objects.filter(id=booking_id).update(
        status=BookingStatus.CONFIRMED, confirmed_at=timezone.now()
    )
    assert post(user_client, offering).json()["status"] == "CONFIRMED"


def test_a_retry_gets_its_booking_even_after_the_request_would_now_be_refused(
    api_client, user, offering
):
    """The first attempt succeeded, so its retry must, even once the slot is too close."""
    slot = future_slot(days=1, hour=10)
    api_client.credentials(HTTP_AUTHORIZATION=bearer(user))
    first = post(api_client, offering, appointment_at=slot.isoformat())
    offering.is_active = False
    offering.save()

    with time_machine.travel(slot - timedelta(minutes=30)):
        api_client.credentials(HTTP_AUTHORIZATION=bearer(user))  # a token valid then
        retry = post(api_client, offering, appointment_at=slot.isoformat())

    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]


def test_the_same_instant_written_with_another_offset_is_the_same_request(user_client, offering):
    slot = future_slot()
    first = post(user_client, offering, appointment_at=slot.isoformat())
    retry = post(user_client, offering, appointment_at=slot.astimezone(UTC).isoformat())
    assert retry.json()["id"] == first.json()["id"]


def test_the_same_key_for_a_different_booking_is_a_422(user_client, offering):
    post(user_client, offering)
    other_time = future_slot(hour=11).isoformat()
    response = post(user_client, offering, appointment_at=other_time)
    assert_problem(response, 422, "IDEMPOTENCY_KEY_REUSED")
    assert Booking.objects.count() == 1


def test_keys_belong_to_one_patient(api_client, offering):
    """Another patient's identical key is theirs: it neither replays nor blocks anything."""
    ids = set()
    for user in (UserFactory(), UserFactory()):
        api_client.credentials(HTTP_AUTHORIZATION=bearer(user))
        response = post(api_client, offering)
        assert response.status_code == 201
        ids.add(response.json()["id"])
    assert len(ids) == 2


def test_a_refused_request_is_not_remembered(user_client, offering):
    """Only a request that created something is replayed; after a refusal the key is free."""
    refused = post(user_client, offering, appointment_at=future_slot(hour=3).isoformat())
    assert_problem(refused, 400, "VALIDATION_ERROR")
    assert post(user_client, offering).status_code == 201


def test_a_malformed_key_is_a_400(user_client, offering):
    body = assert_problem(post(user_client, offering, key="not valid"), 400, "VALIDATION_ERROR")
    assert ("Idempotency-Key", "invalid") in field_errors(body)
    assert not Booking.objects.exists()
