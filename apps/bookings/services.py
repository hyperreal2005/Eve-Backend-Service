"""Booking workflows. Every status change goes through `booking_transition`."""

from datetime import datetime
from uuid import UUID

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.domain import AppointmentPolicy, can_transition, check_appointment
from apps.bookings.errors import CancellationWindowClosed, DuplicateBooking, NotOffered, SlotFull
from apps.bookings.models import (
    ActorType,
    Booking,
    BookingStatus,
    BookingStatusEvent,
    BookingStatusReason,
)
from apps.bookings.selectors import booking_lock, bookings_holding_slots
from apps.catalog.models import Offering
from apps.catalog.selectors import centre_get, diagnostic_test_get
from apps.core.db import advisory_xact_lock, translate_integrity_errors
from apps.core.errors import FieldValidationError, InvalidStateTransition
from apps.core.idempotency import IdempotencyKey
from apps.payments.errors import PaymentInProgress
from apps.payments.models import Payment, PaymentStatus, RefundStatus

log = structlog.get_logger(__name__)


def booking_create(
    *,
    user: User,
    centre_id: UUID,
    test_id: UUID,
    appointment_at: datetime,
    idempotency_key: IdempotencyKey | None = None,
) -> Booking:
    """Book a test for the caller: PENDING, with the price snapshotted and a payment hold.

    With an idempotency key already used by the caller, returns the booking that request made,
    as it is now. That comes first: a retry must get its booking even if, by now, the time is too
    close or the test has been withdrawn.
    """
    now = timezone.now()
    with transaction.atomic():
        if idempotency_key is not None:
            earlier = _booking_for_key(user, idempotency_key)
            if earlier is not None:
                return earlier
        offering = _bookable_offering(centre_id=centre_id, test_id=test_id)
        centre = offering.centre
        violation = check_appointment(
            appointment_at,
            now=now,
            opens_at=centre.opens_at,
            closes_at=centre.closes_at,
            timezone=centre.timezone,
            policy=AppointmentPolicy.from_settings(),
        )
        if violation is not None:
            raise FieldValidationError(
                field="appointment_at", code=violation.code, message=violation.message
            )

        booking = Booking(
            user=user,
            offering=offering,
            appointment_at=appointment_at,
            amount=offering.price,
            currency=offering.currency,
            status=BookingStatus.PENDING,
            hold_expires_at=min(now + settings.BOOKING_HOLD, appointment_at),
            idempotency_key=idempotency_key.value if idempotency_key else "",
            request_fingerprint=idempotency_key.fingerprint if idempotency_key else "",
        )
        if offering.slot_capacity is not None:
            _ensure_place_left(booking, capacity=offering.slot_capacity, now=now)
        # The partial unique index decides duplicates, including two identical requests racing.
        conflicts = {"bookings_one_active_per_slot": lambda: _duplicate_error(booking)}
        with translate_integrity_errors(conflicts):
            booking.save()
        _record_event(booking, from_status="", actor_type=ActorType.USER, actor=user)
    log.info(
        "booking.created",
        booking_id=str(booking.id),
        offering_id=str(offering.id),
        amount=booking.amount,
    )
    return booking


def booking_cancel(*, booking_id: UUID, user: User) -> Booking:
    """Cancel a booking the caller may act on. Cancelling twice is a harmless no-op."""
    with transaction.atomic():
        booking = booking_lock(booking_id=booking_id, user=user)
        if booking.status == BookingStatus.CANCELLED:
            return booking
        if booking.status == BookingStatus.PENDING and _payment_in_flight(booking):
            # Cancelling now would race the payment and could leave money we'd have to refund.
            raise PaymentInProgress()
        if booking.status == BookingStatus.CONFIRMED:
            if booking.appointment_at - timezone.now() < settings.BOOKING_CANCELLATION_CUTOFF:
                raise CancellationWindowClosed()
            _record_refund_owed(booking)
        by_admin = user.is_admin and booking.user_id != user.id
        booking_transition(
            booking,
            to=BookingStatus.CANCELLED,
            reason=(
                BookingStatusReason.ADMIN_CANCELLED
                if by_admin
                else BookingStatusReason.USER_CANCELLED
            ),
            actor_type=ActorType.ADMIN if by_admin else ActorType.USER,
            actor=user,
        )
    return booking


def booking_expire_stale_holds(*, limit: int = 100) -> int:
    """Fail unpaid bookings whose hold has lapsed, releasing their slot. Run by a periodic job.

    Tidiness, not correctness: paying an expired booking is already refused at payment time.
    Bookings with a payment in flight are left alone (elapsed time is no evidence the payment
    failed; reconciliation asks the provider instead). SKIP LOCKED lets several sweepers, or a
    sweeper and a payment, run side by side without waiting on each other.
    """
    in_flight = Payment.objects.filter(booking=OuterRef("pk"), status=PaymentStatus.PENDING)
    with transaction.atomic():
        stale = list(
            Booking.objects.select_for_update(skip_locked=True)
            .filter(status=BookingStatus.PENDING, hold_expires_at__lte=timezone.now())
            .filter(~Exists(in_flight))
            .order_by("hold_expires_at")[:limit]
        )
        for booking in stale:
            booking_transition(
                booking,
                to=BookingStatus.FAILED,
                reason=BookingStatusReason.PAYMENT_TIMEOUT,
                actor_type=ActorType.SYSTEM,
            )
    if stale:
        log.info("bookings.holds_expired", count=len(stale))
    return len(stale)


def booking_transition(
    booking: Booking,
    *,
    to: BookingStatus,
    reason: BookingStatusReason | str = "",
    actor_type: ActorType,
    actor: User | None = None,
    payment: Payment | None = None,
) -> None:
    """The one place a booking's status changes. The caller must hold the booking's row lock.

    Guards the transition, stamps the matching timestamp and appends to the audit trail (with the
    payment that caused it, if any), all in the caller's transaction.
    """
    if not can_transition(booking.status, to):
        raise InvalidStateTransition(
            f"A {booking.status} booking cannot become {to}.",
            booking_id=str(booking.id),
            booking_status=booking.status,
        )
    previous = booking.status
    booking.status = to
    booking.status_reason = reason
    changed = ["status", "status_reason", "updated_at"]
    if to == BookingStatus.CONFIRMED:
        booking.confirmed_at = timezone.now()
        changed.append("confirmed_at")
    elif to == BookingStatus.CANCELLED:
        booking.cancelled_at = timezone.now()
        changed.append("cancelled_at")
    booking.save(update_fields=changed)
    _record_event(
        booking, from_status=previous, actor_type=actor_type, actor=actor, payment=payment
    )
    log.info(
        "booking.transitioned",
        booking_id=str(booking.id),
        from_status=previous,
        to_status=to,
        reason=reason,
        actor_type=actor_type,
    )


def _bookable_offering(*, centre_id: UUID, test_id: UUID) -> Offering:
    offering = (
        Offering.objects.available()
        .select_related("centre", "test")
        .filter(centre_id=centre_id, test_id=test_id)
        .first()
    )
    if offering is None:
        # Say precisely what's wrong: an unknown centre, an unknown test, or no such offer.
        centre_get(centre_id=centre_id)
        diagnostic_test_get(test_id=test_id)
        raise NotOffered()
    return offering


def _booking_for_key(user: User, key: IdempotencyKey) -> Booking | None:
    """The booking an earlier request with this key made, if any. Needs a transaction."""
    key.lock("bookings", user.id)
    booking = Booking.objects.filter(user=user, idempotency_key=key.value).first()
    if booking is not None:
        key.ensure_same_request(booking.request_fingerprint)
        log.info("booking.idempotent_replay", booking_id=str(booking.id))
    return booking


def _ensure_place_left(booking: Booking, *, capacity: int, now: datetime) -> None:
    """Refuse a booking for a full slot.

    The slot is locked until the transaction commits, so two requests for its last place can't
    both count it as free; requests for other slots don't wait. Counting the bookings that hold
    the slot, rather than keeping a counter, means no other path (cancelling, failing, expiring)
    has anything to update.
    """
    advisory_xact_lock(f"slot:{booking.offering_id}:{booking.appointment_at.timestamp():.0f}")
    holders = bookings_holding_slots(offering_id=booking.offering_id, now=now).filter(
        appointment_at=booking.appointment_at
    )
    if holders.count() >= capacity:
        if holders.filter(user=booking.user).exists():
            raise _duplicate_error(booking)  # one of the places taken is the caller's own
        raise SlotFull()


def _duplicate_error(booking: Booking) -> DuplicateBooking:
    existing = (
        Booking.objects.active()
        .filter(user=booking.user, offering=booking.offering, appointment_at=booking.appointment_at)
        .values_list("id", flat=True)
        .first()
    )
    return DuplicateBooking(booking_id=str(existing)) if existing else DuplicateBooking()


def _payment_in_flight(booking: Booking) -> bool:
    return booking.payments.filter(status=PaymentStatus.PENDING).exists()


def _record_refund_owed(booking: Booking) -> None:
    """A paid booking is being cancelled: record the refund we owe (processing it is future work).

    Called with the booking locked; the payment row is locked second, the same order every path
    uses, so this can't deadlock with a webhook for the same payment.
    """
    for payment in booking.payments.select_for_update().filter(status=PaymentStatus.SUCCESS):
        payment.refund_status = RefundStatus.PENDING
        payment.save(update_fields=["refund_status", "updated_at"])
        log.warning(
            "payment.refund_owed",
            payment_id=str(payment.id),
            booking_id=str(booking.id),
            reason="cancelled_after_payment",
        )


def _record_event(
    booking: Booking,
    *,
    from_status: str,
    actor_type: ActorType,
    actor: User | None,
    payment: Payment | None = None,
) -> None:
    BookingStatusEvent.objects.create(
        booking=booking,
        from_status=from_status,
        to_status=booking.status,
        reason=booking.status_reason,
        actor_type=actor_type,
        actor=actor,
        payment=payment,
    )
