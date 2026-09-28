"""Read paths for bookings. Every lookup is scoped to the caller: patients see their own bookings,
administrators see all. Someone else's booking is indistinguishable from a missing one."""

from uuid import UUID

from django.db.models import Prefetch, QuerySet

from apps.accounts.models import User
from apps.bookings.errors import BookingNotFound
from apps.bookings.models import Booking, BookingStatusEvent


def _visible_to(user: User) -> QuerySet[Booking]:
    bookings = Booking.objects.all()
    return bookings if user.is_admin else bookings.filter(user=user)


def booking_list(*, user: User) -> QuerySet[Booking]:
    return _visible_to(user).select_related("offering__centre", "offering__test")


def booking_get(*, booking_id: UUID, user: User) -> Booking:
    """A booking with its catalogue details, payments and history, as the caller may see it."""
    history = BookingStatusEvent.objects.order_by("created_at", "id")
    bookings = booking_list(user=user).prefetch_related(
        Prefetch("status_events", queryset=history), "payments"
    )
    try:
        return bookings.get(id=booking_id)
    except Booking.DoesNotExist:
        raise BookingNotFound() from None


def booking_lock(*, booking_id: UUID, user: User, owner_only: bool = False) -> Booking:
    """SELECT … FOR UPDATE on a booking the caller may act on. Call inside a transaction.

    The booking row is the lock that serialises every change to a booking and its payments.
    With `owner_only`, administrators are treated like anyone else (only the patient pays).
    """
    bookings = Booking.objects.filter(user=user) if owner_only else _visible_to(user)
    try:
        return bookings.select_for_update().get(id=booking_id)
    except Booking.DoesNotExist:
        raise BookingNotFound() from None
