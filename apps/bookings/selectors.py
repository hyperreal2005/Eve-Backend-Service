"""Read paths for bookings. Every lookup is scoped to the caller: patients see their own bookings,
administrators see all. Someone else's booking is indistinguishable from a missing one."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from django.db.models import Count, Exists, OuterRef, Prefetch, Q, QuerySet

from apps.accounts.models import User
from apps.bookings.domain import AppointmentPolicy, check_appointment, day_slots
from apps.bookings.errors import BookingNotFound
from apps.bookings.models import Booking, BookingStatus, BookingStatusEvent
from apps.catalog.models import Offering
from apps.payments.models import Payment, PaymentStatus


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


# ------------------------------------------------------------------------------ slot capacity


def bookings_holding_slots(*, offering_id: UUID, now: datetime) -> QuerySet[Booking]:
    """An offering's bookings that take up their slot.

    Confirmed bookings, and pending ones still holding it: before the hold lapses, or while a
    payment is in flight, since that payment may yet confirm the booking. A lapsed hold frees the
    slot at once, without waiting for the sweeper.
    """
    in_flight = Payment.objects.filter(booking=OuterRef("pk"), status=PaymentStatus.PENDING)
    holding = Q(hold_expires_at__gt=now) | Q(Exists(in_flight))
    return Booking.objects.filter(offering_id=offering_id).filter(
        Q(status=BookingStatus.CONFIRMED) | (Q(status=BookingStatus.PENDING) & holding)
    )


@dataclass(frozen=True)
class Slot:
    start: datetime
    remaining: int | None  # None: no limit
    available: bool


def offering_availability(*, offering: Offering, day: date, now: datetime) -> list[Slot]:
    """Every slot of an offering on `day` (the centre's local date), with the places left.

    A slot is available when a booking for it would be accepted right now: the same appointment
    rules `booking_create` applies, plus a place left. One grouped query counts the places taken.
    """
    centre = offering.centre
    policy = AppointmentPolicy.from_settings()
    starts = day_slots(
        day,
        opens_at=centre.opens_at,
        closes_at=centre.closes_at,
        timezone=centre.timezone,
        slot_minutes=policy.slot_minutes,
    )
    taken: dict[datetime, int] = {}
    if offering.slot_capacity is not None and starts:
        taken = dict(
            bookings_holding_slots(offering_id=offering.id, now=now)
            .filter(appointment_at__range=(starts[0], starts[-1]))
            .values_list("appointment_at")
            .annotate(count=Count("id"))
            .order_by()
        )

    slots = []
    for start in starts:
        remaining = (
            None
            if offering.slot_capacity is None
            else max(offering.slot_capacity - taken.get(start, 0), 0)
        )
        bookable = (
            check_appointment(
                start,
                now=now,
                opens_at=centre.opens_at,
                closes_at=centre.closes_at,
                timezone=centre.timezone,
                policy=policy,
            )
            is None
        )
        slots.append(Slot(start=start, remaining=remaining, available=bookable and remaining != 0))
    return slots
