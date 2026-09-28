"""The database rejects inconsistent bookings, whatever path the write takes."""

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.tests.factories import BookingFactory

pytestmark = pytest.mark.django_db


def assert_rejected(write) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        write()


def test_one_active_booking_per_user_offering_and_time():
    booking = BookingFactory()
    same_slot = {
        "user": booking.user,
        "offering": booking.offering,
        "appointment_at": booking.appointment_at,
    }
    assert_rejected(lambda: BookingFactory(**same_slot))
    assert_rejected(lambda: BookingFactory(**same_slot, status=BookingStatus.CONFIRMED))
    # Once the first booking is no longer active, the same slot can be booked again.
    Booking.objects.filter(pk=booking.pk).update(
        status=BookingStatus.CANCELLED, cancelled_at=timezone.now()
    )
    assert BookingFactory(**same_slot)


def test_amount_must_be_positive():
    booking = BookingFactory()
    assert_rejected(lambda: Booking.objects.filter(pk=booking.pk).update(amount=0))


def test_status_and_reason_must_be_known():
    booking = BookingFactory()
    assert_rejected(lambda: Booking.objects.filter(pk=booking.pk).update(status="PAID"))
    assert_rejected(lambda: Booking.objects.filter(pk=booking.pk).update(status_reason="BORED"))


def test_confirmed_and_cancelled_bookings_carry_their_timestamps():
    booking = BookingFactory()
    assert_rejected(
        lambda: Booking.objects.filter(pk=booking.pk).update(status=BookingStatus.CONFIRMED)
    )
    assert_rejected(
        lambda: Booking.objects.filter(pk=booking.pk).update(status=BookingStatus.CANCELLED)
    )
