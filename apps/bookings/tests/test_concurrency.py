"""Races only a real database can arbitrate. Each thread is a separate client and connection."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import UserFactory, bearer
from apps.bookings.models import Booking
from apps.bookings.tests.factories import future_slot
from apps.catalog.tests.factories import OfferingFactory
from apps.core.tests.concurrency import run_concurrently

pytestmark = pytest.mark.django_db(transaction=True)


def test_identical_bookings_submitted_at_the_same_instant_create_exactly_one():
    user, offering = UserFactory(), OfferingFactory()
    header = bearer(user)
    data = {
        "centre_id": str(offering.centre_id),
        "test_id": str(offering.test_id),
        "appointment_at": future_slot().isoformat(),
    }

    def submit(_: int) -> int:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=header)
        return client.post("/api/v1/bookings/", data, format="json").status_code

    statuses = run_concurrently(8, submit)

    assert sorted(statuses) == [201] + [409] * 7
    assert Booking.objects.count() == 1


def test_eight_patients_racing_for_a_two_place_slot_get_exactly_two_places():
    offering = OfferingFactory(slot_capacity=2)
    headers = [bearer(UserFactory()) for _ in range(8)]
    data = {
        "centre_id": str(offering.centre_id),
        "test_id": str(offering.test_id),
        "appointment_at": future_slot().isoformat(),
    }

    def submit(index: int) -> tuple[int, str | None]:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=headers[index])
        response = client.post("/api/v1/bookings/", data, format="json")
        return response.status_code, response.json().get("code")

    results = run_concurrently(8, submit)

    assert sorted(results, key=str) == [(201, None)] * 2 + [(409, "SLOT_FULL")] * 6
    assert Booking.objects.count() == 2


def test_eight_simultaneous_retries_with_one_idempotency_key_all_get_the_one_booking():
    user, offering = UserFactory(), OfferingFactory()
    header = bearer(user)
    data = {
        "centre_id": str(offering.centre_id),
        "test_id": str(offering.test_id),
        "appointment_at": future_slot().isoformat(),
    }

    def submit(_: int) -> tuple[int, str]:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=header)
        response = client.post("/api/v1/bookings/", data, format="json", HTTP_IDEMPOTENCY_KEY="k1")
        return response.status_code, response.json()["id"]

    results = run_concurrently(8, submit)

    assert {status for status, _ in results} == {201}
    assert len({booking_id for _, booking_id in results}) == 1
    assert Booking.objects.count() == 1
