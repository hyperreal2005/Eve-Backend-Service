"""Slot capacity: how many patients one appointment slot can take, and the availability view."""

from datetime import UTC, datetime, time, timedelta

import pytest
import time_machine
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.bookings.errors import DuplicateBooking, SlotFull
from apps.bookings.models import BookingStatus
from apps.bookings.services import booking_create
from apps.bookings.tests.factories import IST, BookingFactory, future_slot
from apps.catalog.tests.factories import OfferingFactory
from apps.core.tests.assertions import assert_problem, field_errors, query_count
from apps.payments.tests.factories import PaymentFactory

pytestmark = pytest.mark.django_db

SLOT = future_slot(days=2, hour=10)


def book(offering, user=None, appointment_at=SLOT):
    return booking_create(
        user=user or UserFactory(),
        centre_id=offering.centre_id,
        test_id=offering.test_id,
        appointment_at=appointment_at,
    )


def lapsed(**fields):
    """A PENDING booking whose payment hold has run out (the sweeper hasn't run yet)."""
    return BookingFactory(hold_expires_at=timezone.now() - timedelta(minutes=1), **fields)


# ---------------------------------------------------------------------------------- booking


def test_a_slot_takes_as_many_patients_as_its_capacity_and_no_more():
    offering = OfferingFactory(slot_capacity=2)
    book(offering)
    book(offering)
    with pytest.raises(SlotFull):
        book(offering)


def test_without_a_capacity_a_slot_has_no_limit():
    offering = OfferingFactory(slot_capacity=None)
    for _ in range(3):
        book(offering)


def test_other_slots_and_other_offerings_are_unaffected_by_a_full_slot():
    offering = OfferingFactory(slot_capacity=1)
    book(offering)
    book(offering, appointment_at=SLOT + timedelta(minutes=15))
    book(OfferingFactory(slot_capacity=1, centre=offering.centre))


def test_a_patient_already_holding_a_place_is_told_it_is_a_duplicate_not_that_the_slot_is_full():
    offering, user = OfferingFactory(slot_capacity=1), UserFactory()
    first = book(offering, user)
    with pytest.raises(DuplicateBooking) as raised:
        book(offering, user)
    assert raised.value.extra == {"booking_id": str(first.id)}


def test_a_confirmed_booking_holds_its_place():
    offering = OfferingFactory(slot_capacity=1)
    BookingFactory(
        offering=offering,
        appointment_at=SLOT,
        status=BookingStatus.CONFIRMED,
        hold_expires_at=timezone.now() - timedelta(hours=1),  # irrelevant once paid
    )
    with pytest.raises(SlotFull):
        book(offering)


@pytest.mark.parametrize("status", [BookingStatus.CANCELLED, BookingStatus.FAILED])
def test_cancelled_and_failed_bookings_free_their_place(status):
    offering = OfferingFactory(slot_capacity=1)
    BookingFactory(offering=offering, appointment_at=SLOT, status=status)
    book(offering)


def test_a_lapsed_hold_frees_its_place_at_once_without_waiting_for_the_sweeper():
    offering = OfferingFactory(slot_capacity=1)
    lapsed(offering=offering, appointment_at=SLOT)
    book(offering)


def test_a_lapsed_hold_with_a_payment_in_flight_keeps_its_place():
    """That payment may still succeed and confirm the booking: giving the place away could
    overbook the slot."""
    offering = OfferingFactory(slot_capacity=1)
    PaymentFactory(booking=lapsed(offering=offering, appointment_at=SLOT))
    with pytest.raises(SlotFull):
        book(offering)


def test_booking_a_full_slot_is_a_409(user_client):
    offering = OfferingFactory(slot_capacity=1)
    book(offering)
    payload = {
        "centre_id": str(offering.centre_id),
        "test_id": str(offering.test_id),
        "appointment_at": SLOT.isoformat(),
    }
    assert_problem(user_client.post("/api/v1/bookings/", payload), 409, "SLOT_FULL")


# ----------------------------------------------------------------------------- availability

# 04:30 UTC: 10:00 in India. The factory centre is open 07:00-21:00 India time.
NOW = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)
TODAY, LATER = NOW.astimezone(IST).date(), (NOW + timedelta(days=2)).astimezone(IST).date()


def availability_url(offering, day=None) -> str:
    url = f"/api/v1/centres/{offering.centre_id}/tests/{offering.test_id}/availability/"
    return url if day is None else f"{url}?date={day.isoformat()}"


def local(day, hour, minute=0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


def slot_at(body, hour, minute=0) -> dict:
    """The slot starting at hour:minute India time."""
    [slot] = [
        s
        for s in body["slots"]
        if datetime.fromisoformat(s["start"]).astimezone(IST).time() == time(hour, minute)
    ]
    return slot


@time_machine.travel(NOW, tick=False)
def test_availability_lists_every_slot_of_the_day_with_the_places_left(api_client):
    offering = OfferingFactory(slot_capacity=2)
    at_ten = local(LATER, 10)
    BookingFactory(offering=offering, appointment_at=at_ten)
    BookingFactory.create_batch(2, offering=offering, appointment_at=at_ten + timedelta(minutes=15))

    response = api_client.get(availability_url(offering, LATER))

    assert response.status_code == 200
    body = response.json()
    assert (body["date"], body["timezone"], body["slot_minutes"], body["slot_capacity"]) == (
        LATER.isoformat(),
        "Asia/Kolkata",
        15,
        2,
    )
    assert len(body["slots"]) == 56
    assert body["slots"][0]["start"] == "2026-10-03T01:30:00Z"  # 07:00 India
    assert slot_at(body, 10, 0) == {
        "start": "2026-10-03T04:30:00Z",
        "remaining": 1,
        "available": True,
    }
    assert slot_at(body, 10, 15)["remaining"] == 0
    assert slot_at(body, 10, 15)["available"] is False
    assert slot_at(body, 10, 30) == {
        "start": "2026-10-03T05:00:00Z",
        "remaining": 2,
        "available": True,
    }


@time_machine.travel(NOW, tick=False)
def test_slots_a_booking_would_be_refused_for_are_unavailable(api_client):
    offering = OfferingFactory(slot_capacity=None)

    body = api_client.get(availability_url(offering, TODAY)).json()

    # It is 10:00 in India and bookings need an hour's notice: 11:00 is the first bookable slot.
    assert slot_at(body, 10, 45)["available"] is False
    assert slot_at(body, 11, 0) == {
        "start": "2026-10-01T05:30:00Z",
        "remaining": None,
        "available": True,
    }
    assert body["slot_capacity"] is None


@time_machine.travel(NOW, tick=False)
def test_availability_counts_only_bookings_that_hold_their_place(api_client):
    offering = OfferingFactory(slot_capacity=1)
    at_ten = local(LATER, 10)
    lapsed(offering=offering, appointment_at=at_ten)
    BookingFactory(offering=offering, appointment_at=at_ten, status=BookingStatus.CANCELLED)

    body = api_client.get(availability_url(offering, LATER)).json()

    assert slot_at(body, 10)["remaining"] == 1


def test_availability_needs_a_valid_date(api_client):
    offering = OfferingFactory()
    body = assert_problem(api_client.get(availability_url(offering)), 400, "VALIDATION_ERROR")
    assert ("date", "required") in field_errors(body)
    response = api_client.get(availability_url(offering) + "?date=2026-02-30")
    assert ("date", "invalid") in field_errors(assert_problem(response, 400, "VALIDATION_ERROR"))


def test_availability_of_something_not_offered_is_a_404(api_client):
    offering = OfferingFactory(is_active=False)
    response = api_client.get(availability_url(offering, LATER))
    assert_problem(response, 404, "OFFERING_NOT_FOUND")
    other = OfferingFactory(centre__is_active=False)
    assert_problem(api_client.get(availability_url(other, LATER)), 404, "CENTRE_NOT_FOUND")


@time_machine.travel(NOW, tick=False)
def test_availability_query_count_does_not_grow_with_bookings(api_client):
    offering = OfferingFactory(slot_capacity=50)
    at_ten = local(LATER, 10)
    url = availability_url(offering, LATER)
    few = query_count(lambda: api_client.get(url))
    for minutes in range(0, 300, 15):
        BookingFactory.create_batch(
            2, offering=offering, appointment_at=at_ten + timedelta(minutes=minutes)
        )
    assert query_count(lambda: api_client.get(url)) == few
