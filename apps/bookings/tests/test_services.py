import uuid
from datetime import timedelta

import pytest
import time_machine
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.bookings.errors import CancellationWindowClosed, DuplicateBooking, NotOffered
from apps.bookings.models import ActorType, BookingStatus, BookingStatusReason
from apps.bookings.services import booking_cancel, booking_create, booking_transition
from apps.bookings.tests.factories import BookingFactory, future_slot
from apps.catalog.errors import CentreNotFound, DiagnosticTestNotFound
from apps.catalog.services import offering_update
from apps.catalog.tests.factories import DiagnosticCentreFactory, OfferingFactory
from apps.core.errors import FieldValidationError, InvalidStateTransition

pytestmark = pytest.mark.django_db


def book(user, offering, **overrides):
    return booking_create(
        user=user,
        centre_id=overrides.get("centre_id", offering.centre_id),
        test_id=overrides.get("test_id", offering.test_id),
        appointment_at=overrides.get("appointment_at", future_slot()),
    )


# ------------------------------------------------------------------------------------ create


def test_a_booking_starts_pending_with_the_price_snapshotted_and_a_hold():
    offering = OfferingFactory(price=650_000)
    before = timezone.now()

    booking = book(UserFactory(), offering)

    assert (booking.status, booking.amount, booking.currency) == ("PENDING", 650_000, "INR")
    hold = booking.hold_expires_at - before
    assert timedelta(minutes=14) < hold <= timedelta(minutes=15, seconds=5)
    [event] = booking.status_events.all()
    assert (event.from_status, event.to_status, event.actor_type) == ("", "PENDING", "USER")


def test_a_later_price_change_does_not_touch_existing_bookings():
    offering = OfferingFactory(price=650_000)
    booking = book(UserFactory(), offering)
    offering_update(offering=offering, changes={"price": 900_000})
    booking.refresh_from_db()
    assert booking.amount == 650_000


def test_the_error_says_precisely_what_is_not_bookable():
    user, offering = UserFactory(), OfferingFactory()
    with pytest.raises(CentreNotFound):
        book(user, offering, centre_id=uuid.uuid4())
    with pytest.raises(DiagnosticTestNotFound):
        book(user, offering, test_id=uuid.uuid4())
    with pytest.raises(NotOffered):
        book(user, offering, centre_id=DiagnosticCentreFactory().id)


@pytest.mark.parametrize(
    ("inactive", "error"),
    [
        ({"is_active": False}, NotOffered),
        ({"centre__is_active": False}, CentreNotFound),  # inactive centres are hidden
        ({"test__is_active": False}, DiagnosticTestNotFound),
    ],
)
def test_withdrawn_offerings_cannot_be_booked(inactive, error):
    offering = OfferingFactory(**inactive)
    with pytest.raises(error):
        book(UserFactory(), offering)


def test_appointment_rules_are_reported_as_field_errors():
    with pytest.raises(FieldValidationError) as caught:
        book(UserFactory(), OfferingFactory(), appointment_at=future_slot(hour=23))
    [error] = caught.value.extra["errors"]
    assert (error["field"], error["code"]) == ("appointment_at", "outside_opening_hours")


def test_a_duplicate_names_the_existing_booking():
    user, offering, slot = UserFactory(), OfferingFactory(), future_slot()
    first = book(user, offering, appointment_at=slot)
    with pytest.raises(DuplicateBooking) as caught:
        book(user, offering, appointment_at=slot)
    assert caught.value.extra == {"booking_id": str(first.id)}


def test_the_same_slot_can_be_booked_again_after_cancelling():
    user, offering, slot = UserFactory(), OfferingFactory(), future_slot()
    first = book(user, offering, appointment_at=slot)
    booking_cancel(booking_id=first.id, user=user)
    assert book(user, offering, appointment_at=slot).id != first.id


# ------------------------------------------------------------------------------------ cancel


def test_cancelling_a_pending_booking_is_recorded():
    booking = BookingFactory()
    booking_cancel(booking_id=booking.id, user=booking.user)
    booking.refresh_from_db()
    assert (booking.status, booking.status_reason) == ("CANCELLED", "USER_CANCELLED")
    assert booking.cancelled_at is not None
    [event] = booking.status_events.all()
    assert (event.from_status, event.to_status, event.actor_id) == (
        "PENDING",
        "CANCELLED",
        booking.user_id,
    )


def test_cancelling_twice_is_a_no_op():
    booking = BookingFactory()
    booking_cancel(booking_id=booking.id, user=booking.user)
    booking_cancel(booking_id=booking.id, user=booking.user)
    assert booking.status_events.count() == 1


def test_a_failed_booking_cannot_be_cancelled():
    booking = BookingFactory(status=BookingStatus.FAILED)
    with pytest.raises(InvalidStateTransition):
        booking_cancel(booking_id=booking.id, user=booking.user)


def test_a_confirmed_booking_can_be_cancelled_until_the_cutoff():
    booking = BookingFactory(
        status=BookingStatus.CONFIRMED, appointment_at=timezone.now() + timedelta(hours=3)
    )
    # 61 minutes later the appointment is under 2 hours away: too late.
    one_hour_later = time_machine.travel(timezone.now() + timedelta(minutes=61), tick=False)
    with one_hour_later, pytest.raises(CancellationWindowClosed):
        booking_cancel(booking_id=booking.id, user=booking.user)
    # Now (3 hours ahead) it's still allowed.
    booking_cancel(booking_id=booking.id, user=booking.user)
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED


def test_an_administrator_cancelling_is_recorded_as_such():
    booking = BookingFactory()
    admin = UserFactory(role=Role.ADMIN)
    booking_cancel(booking_id=booking.id, user=admin)
    booking.refresh_from_db()
    assert booking.status_reason == BookingStatusReason.ADMIN_CANCELLED
    assert booking.status_events.get().actor_type == ActorType.ADMIN


def test_confirming_stamps_the_time_and_records_the_actor():
    booking = BookingFactory()
    booking_transition(booking, to=BookingStatus.CONFIRMED, actor_type=ActorType.PROVIDER)
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.confirmed_at is not None
    event = booking.status_events.get()
    assert (event.to_status, event.actor_type, event.actor_id) == ("CONFIRMED", "PROVIDER", None)


def test_the_transition_guard_protects_terminal_states():
    booking = BookingFactory(status=BookingStatus.CANCELLED)
    with pytest.raises(InvalidStateTransition):
        booking_transition(booking, to=BookingStatus.CONFIRMED, actor_type=ActorType.SYSTEM)
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED
