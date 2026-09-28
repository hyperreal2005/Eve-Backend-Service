import uuid
from datetime import datetime

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.bookings.models import Booking, BookingStatus
from apps.bookings.tests.factories import BookingFactory, future_slot
from apps.catalog.tests.factories import DiagnosticCentreFactory, OfferingFactory
from apps.core.tests.assertions import assert_problem, field_errors, query_count

pytestmark = pytest.mark.django_db

BOOKINGS_URL = "/api/v1/bookings/"


def booking_url(booking_id) -> str:
    return f"{BOOKINGS_URL}{booking_id}/"


def cancel_url(booking_id) -> str:
    return f"{booking_url(booking_id)}cancel/"


def payload(offering, **overrides) -> dict:
    return {
        "centre_id": str(offering.centre_id),
        "test_id": str(offering.test_id),
        "appointment_at": future_slot().isoformat(),
        **overrides,
    }


# ------------------------------------------------------------------------------------ create


def test_a_patient_books_a_test(user_client, user):
    offering = OfferingFactory(price=650_000)
    slot = future_slot(hour=11, minute=30)

    response = user_client.post(BOOKINGS_URL, payload(offering, appointment_at=slot.isoformat()))

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["status_reason"], body["user_id"]) == (
        "PENDING",
        None,
        str(user.id),
    )
    assert (body["amount"], body["currency"]) == (650_000, "INR")
    assert body["centre"]["id"] == str(offering.centre_id)
    assert body["test"]["id"] == str(offering.test_id)
    # Stored as an instant and returned in UTC: 11:30 India time is 06:00Z.
    assert body["appointment_at"].endswith("06:00:00Z")
    assert datetime.fromisoformat(body["appointment_at"]) == slot
    assert [entry["to_status"] for entry in body["history"]] == ["PENDING"]
    assert response["Location"].endswith(booking_url(body["id"]))


def test_booking_requires_authentication(api_client):
    response = api_client.post(BOOKINGS_URL, payload(OfferingFactory()))
    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")


@pytest.mark.parametrize("field", ["amount", "status", "user_id", "hold_expires_at"])
def test_server_owned_fields_cannot_be_sent(user_client, field):
    response = user_client.post(BOOKINGS_URL, {**payload(OfferingFactory()), field: "1"})
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert (field, "unknown_field") in field_errors(body)
    assert not Booking.objects.exists()


@pytest.mark.parametrize(
    ("appointment_at", "code"),
    [
        (lambda: future_slot().replace(tzinfo=None).isoformat(), "timezone_required"),
        (lambda: future_slot(days=-1).isoformat(), "in_past"),
        (lambda: future_slot(days=45).isoformat(), "too_far_ahead"),
        (lambda: future_slot(minute=7).isoformat(), "not_on_slot_boundary"),
        (lambda: future_slot(hour=23).isoformat(), "outside_opening_hours"),
        (lambda: "tomorrow at ten", "invalid"),
    ],
)
def test_appointment_times_are_validated(user_client, appointment_at, code):
    offering = OfferingFactory()
    response = user_client.post(BOOKINGS_URL, payload(offering, appointment_at=appointment_at()))
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert ("appointment_at", code) in field_errors(body)


def test_a_test_the_centre_does_not_offer_is_unprocessable(user_client):
    offering = OfferingFactory()
    other_centre = DiagnosticCentreFactory()
    response = user_client.post(BOOKINGS_URL, payload(offering, centre_id=str(other_centre.id)))
    assert_problem(response, 422, "TEST_NOT_OFFERED")


def test_unknown_references_are_404s(user_client):
    offering = OfferingFactory()
    response = user_client.post(BOOKINGS_URL, payload(offering, test_id=str(uuid.uuid4())))
    assert_problem(response, 404, "TEST_NOT_FOUND")


def test_a_double_submit_is_a_conflict_that_names_the_original(user_client):
    data = payload(OfferingFactory())
    first = user_client.post(BOOKINGS_URL, data)
    body = assert_problem(user_client.post(BOOKINGS_URL, data), 409, "DUPLICATE_BOOKING")
    assert body["booking_id"] == first.json()["id"]
    assert Booking.objects.count() == 1


# ------------------------------------------------------------------------------ list & detail


def test_patients_list_only_their_own_bookings_newest_first(user_client, user):
    older, newer = BookingFactory(user=user), BookingFactory(user=user)
    BookingFactory()  # someone else's

    body = user_client.get(BOOKINGS_URL).json()

    assert [item["id"] for item in body["results"]] == [str(newer.id), str(older.id)]
    assert set(body) == {"next", "previous", "results"}  # cursor pagination


def test_administrators_list_every_booking(admin_client):
    BookingFactory.create_batch(3)
    assert len(admin_client.get(BOOKINGS_URL).json()["results"]) == 3


def test_bookings_filter_by_status(user_client, user):
    confirmed = BookingFactory(user=user, status=BookingStatus.CONFIRMED)
    BookingFactory(user=user)
    body = user_client.get(BOOKINGS_URL, {"status": "CONFIRMED"}).json()
    assert [item["id"] for item in body["results"]] == [str(confirmed.id)]


def test_booking_list_query_count_does_not_grow_with_rows(user_client, user):
    BookingFactory.create_batch(2, user=user)
    few = query_count(lambda: user_client.get(BOOKINGS_URL))
    BookingFactory.create_batch(8, user=user)
    assert query_count(lambda: user_client.get(BOOKINGS_URL)) == few


def test_a_patient_reads_their_booking_with_its_history(user_client, user):
    booking = BookingFactory(user=user)
    response = user_client.get(booking_url(booking.id))
    assert response.status_code == 200
    assert response.json()["id"] == str(booking.id)


def test_another_patients_booking_looks_like_a_missing_one(user_client):
    someone_elses = BookingFactory()
    assert_problem(user_client.get(booking_url(someone_elses.id)), 404, "BOOKING_NOT_FOUND")
    assert_problem(user_client.get(booking_url(uuid.uuid4())), 404, "BOOKING_NOT_FOUND")
    assert_problem(user_client.post(cancel_url(someone_elses.id)), 404, "BOOKING_NOT_FOUND")


def test_status_cannot_be_written_directly(user_client, user):
    booking = BookingFactory(user=user)
    response = user_client.patch(booking_url(booking.id), {"status": "CONFIRMED"})
    assert_problem(response, 405, "METHOD_NOT_ALLOWED")


# ------------------------------------------------------------------------------------ cancel


def test_a_patient_cancels_their_booking(user_client, user):
    booking = BookingFactory(user=user)
    response = user_client.post(cancel_url(booking.id))
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["status_reason"]) == ("CANCELLED", "USER_CANCELLED")
    assert [entry["to_status"] for entry in body["history"]] == ["CANCELLED"]
    # Cancelling again is harmless and returns the same booking.
    again = user_client.post(cancel_url(booking.id))
    assert (again.status_code, again.json()["status"]) == (200, "CANCELLED")


def test_a_failed_booking_cannot_be_cancelled(user_client, user):
    booking = BookingFactory(user=user, status=BookingStatus.FAILED)
    assert_problem(user_client.post(cancel_url(booking.id)), 409, "INVALID_STATE_TRANSITION")


def test_an_administrator_can_cancel_any_booking(admin_client):
    booking = BookingFactory(user=UserFactory())
    response = admin_client.post(cancel_url(booking.id))
    assert response.json()["status_reason"] == "ADMIN_CANCELLED"
