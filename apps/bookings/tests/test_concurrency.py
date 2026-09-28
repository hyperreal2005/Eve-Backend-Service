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
