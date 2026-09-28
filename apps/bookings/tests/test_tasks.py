"""The hold sweeper: fails unpaid bookings whose hold lapsed, and nothing else."""

import threading
from datetime import timedelta

import pytest
from django.db import connections, transaction
from django.utils import timezone

from apps.bookings.models import ActorType, Booking, BookingStatus, BookingStatusReason
from apps.bookings.tasks import expire_stale_holds
from apps.bookings.tests.factories import BookingFactory
from apps.payments.tests.factories import PaymentFactory


def lapsed() -> dict:
    return {"hold_expires_at": timezone.now() - timedelta(minutes=1)}


@pytest.mark.django_db
def test_lapsed_unpaid_bookings_are_failed_and_recorded():
    booking = BookingFactory(**lapsed())

    assert expire_stale_holds() == 1

    booking.refresh_from_db()
    assert (booking.status, booking.status_reason) == (
        BookingStatus.FAILED,
        BookingStatusReason.PAYMENT_TIMEOUT,
    )
    assert booking.status_events.get().actor_type == ActorType.SYSTEM
    assert expire_stale_holds() == 0  # idempotent


@pytest.mark.django_db
def test_everything_else_is_left_alone():
    fresh = BookingFactory()
    paying = BookingFactory(**lapsed())
    PaymentFactory(booking=paying)  # a payment in flight keeps the hold
    confirmed = BookingFactory(**lapsed(), status=BookingStatus.CONFIRMED)

    assert expire_stale_holds() == 0
    assert set(Booking.objects.values_list("id", "status")) == {
        (fresh.id, BookingStatus.PENDING),
        (paying.id, BookingStatus.PENDING),
        (confirmed.id, BookingStatus.CONFIRMED),
    }


@pytest.mark.django_db(transaction=True)
def test_a_booking_locked_by_someone_else_is_skipped_not_waited_for():
    booking = BookingFactory(**lapsed())
    locked, release = threading.Event(), threading.Event()

    def hold_the_lock() -> None:
        with transaction.atomic():
            Booking.objects.select_for_update().get(id=booking.id)
            locked.set()
            release.wait(timeout=10)
        connections.close_all()

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    locked.wait(timeout=10)
    try:
        assert expire_stale_holds() == 0  # SKIP LOCKED: returns at once instead of blocking
    finally:
        release.set()
        holder.join()
    assert expire_stale_holds() == 1
