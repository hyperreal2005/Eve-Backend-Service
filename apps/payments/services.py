"""Payment workflows.

Every result a provider reports, whichever way it arrives, is applied by `payment_apply_result`,
which locks booking then payment (the one lock order in the codebase) and asks `decide`.
"""

from collections import Counter
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

import structlog
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import ActorType, Booking, BookingStatus, BookingStatusReason
from apps.bookings.selectors import booking_lock
from apps.bookings.services import booking_transition
from apps.core.db import translate_integrity_errors
from apps.payments.domain import Outcome, decide
from apps.payments.errors import (
    BookingAlreadyPaid,
    BookingExpired,
    BookingNotPayable,
    PaymentInProgress,
    ProviderResultRejected,
)
from apps.payments.gateway import GatewayError, PaymentGateway, PaymentResult, get_gateway
from apps.payments.models import Payment, PaymentStatus, RefundStatus

log = structlog.get_logger(__name__)


class Source(StrEnum):
    """How we learned a payment's result."""

    GATEWAY = "gateway"  # the provider's answer to our charge request
    WEBHOOK = "webhook"  # a signed event pushed by the provider
    RECONCILIATION = "reconciliation"  # we asked the provider about a stuck payment


_ACTOR = {
    Source.GATEWAY: ActorType.PROVIDER,
    Source.WEBHOOK: ActorType.PROVIDER,
    Source.RECONCILIATION: ActorType.SYSTEM,
}


def payment_initiate(*, user: User, booking_id: UUID, method: str | None = None) -> Payment:
    """Pay for one of the caller's bookings, in three steps.

    A. Lock the booking, check it's payable and record the payment as PENDING. Commit.
    B. Ask the provider to charge, holding no locks: a slow provider must not block others.
    C. Apply the provider's answer through `payment_apply_result`.

    A crash after A or B leaves a PENDING payment that the webhook or reconciliation completes,
    because the provider knows the charge by our payment id. If the provider can't be reached,
    the outcome is unknown, not failed: the payment stays PENDING.
    """
    gateway = get_gateway()
    payment = _record_intent(
        user=user, booking_id=booking_id, gateway=gateway, method=method or gateway.default_method
    )
    try:
        result = gateway.charge(
            reference=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            method=payment.method,
        )
    except GatewayError as exc:
        log.warning("payment.outcome_unknown", payment_id=str(payment.id), error=str(exc))
        return payment
    payment_apply_result(payment_id=payment.id, result=result, source=Source.GATEWAY)
    payment.refresh_from_db()
    return payment


def payment_apply_result(
    *,
    payment_id: UUID,
    result: PaymentResult,
    source: Source,
    expected_amount: int | None = None,
    expected_currency: str | None = None,
) -> Outcome:
    """Apply a provider's verdict on a payment. Idempotent, and safe in any order.

    Raises ProviderResultRejected (changing nothing) for a result we can't trust: an unknown
    payment, or one whose amount or provider id contradicts ours.
    """
    with transaction.atomic():
        booking_id = (
            Payment.objects.filter(id=payment_id).values_list("booking_id", flat=True).first()
        )
        if booking_id is None:
            raise ProviderResultRejected("unknown_payment")
        booking = Booking.objects.select_for_update().get(id=booking_id)
        payment = Payment.objects.select_for_update().get(id=payment_id)
        _check_consistency(payment, result, expected_amount, expected_currency)

        decision = decide(payment.status, result.status, booking.status)
        changed: set[str] = set()
        if result.provider_payment_id and not payment.provider_payment_id:
            payment.provider_payment_id = result.provider_payment_id
            changed.add("provider_payment_id")
        if decision.payment_to is not None:
            payment.status = decision.payment_to
            payment.completed_at = timezone.now()
            failed = decision.payment_to == PaymentStatus.FAILED
            payment.failure_code = (result.failure_code or "declined") if failed else ""
            payment.failure_message = result.failure_message if failed else ""
            changed |= {"status", "completed_at", "failure_code", "failure_message"}
        if decision.refund_owed:
            payment.refund_status = RefundStatus.PENDING
            changed.add("refund_status")
        if changed:
            payment.save(update_fields=[*sorted(changed), "updated_at"])
        if decision.booking_to is not None:
            booking_transition(
                booking,
                to=decision.booking_to,
                reason=(
                    BookingStatusReason.PAYMENT_DECLINED
                    if decision.booking_to == BookingStatus.FAILED
                    else ""
                ),
                actor_type=_ACTOR[source],
                payment=payment,
            )
    _log_outcome(payment, decision.outcome, source)
    return decision.outcome


def payment_reconcile_stale(*, older_than: timedelta, limit: int = 100) -> dict[str, int]:
    """Ask the provider about payments still PENDING after `older_than`. Run by a periodic job.

    Covers lost responses and webhooks that never arrived. "The provider has no such charge"
    is a provider fact (we crashed before charging), so it counts as FAILED. "Still processing"
    is left for next time. Answers go through `payment_apply_result`, so a webhook racing this
    job for the same payment is harmless.
    """
    cutoff = timezone.now() - older_than
    stale = list(
        Payment.objects.filter(status=PaymentStatus.PENDING, created_at__lte=cutoff)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )
    gateway = get_gateway()
    tally: Counter[str] = Counter()
    for payment_id in stale:
        try:
            result = gateway.fetch(reference=payment_id)
        except GatewayError:
            tally["provider_unreachable"] += 1
            continue
        if result is None:
            result = PaymentResult(
                status=PaymentStatus.FAILED,
                failure_code="not_found_at_provider",
                failure_message="The provider has no record of this charge.",
            )
        elif result.status == PaymentStatus.PENDING:
            tally["still_pending"] += 1
            continue
        try:
            tally[
                payment_apply_result(
                    payment_id=payment_id, result=result, source=Source.RECONCILIATION
                )
            ] += 1
        except ProviderResultRejected as exc:
            log.error(
                "payment.reconciliation_rejected", payment_id=str(payment_id), reason=exc.reason
            )
            tally["rejected"] += 1
    if stale:
        log.info("payments.reconciled", checked=len(stale), **tally)
    return dict(tally)


def _record_intent(
    *, user: User, booking_id: UUID, gateway: PaymentGateway, method: str
) -> Payment:
    expired = False
    with transaction.atomic():
        # Only the patient pays for their booking; anyone else gets the same 404 as a missing one.
        booking = booking_lock(booking_id=booking_id, user=user, owner_only=True)
        if _hold_expired(booking):
            # Enforced here, not left to the sweeper: correctness never waits for a job.
            booking_transition(
                booking,
                to=BookingStatus.FAILED,
                reason=BookingStatusReason.PAYMENT_TIMEOUT,
                actor_type=ActorType.SYSTEM,
            )
            expired = True
        else:
            _ensure_payable(booking)
            payment = Payment(
                booking=booking,
                amount=booking.amount,  # never from the client
                currency=booking.currency,
                method=method,
                provider=gateway.name,
            )
            # Backstop for the check above: at most one live payment per booking, even in a race.
            with translate_integrity_errors({"payments_one_live_per_booking": PaymentInProgress}):
                payment.save()
    if expired:  # raised after the commit, so the booking stays FAILED
        raise BookingExpired(booking_id=str(booking_id))
    log.info(
        "payment.initiated",
        payment_id=str(payment.id),
        booking_id=str(booking_id),
        amount=payment.amount,
        method=method,
    )
    return payment


def _hold_expired(booking: Booking) -> bool:
    # A booking whose payment is still in flight keeps its hold until that payment resolves.
    return (
        booking.status == BookingStatus.PENDING
        and timezone.now() >= booking.hold_expires_at
        and not booking.payments.filter(status=PaymentStatus.PENDING).exists()
    )


def _ensure_payable(booking: Booking) -> None:
    if booking.status == BookingStatus.CONFIRMED:
        raise BookingAlreadyPaid(booking_id=str(booking.id))
    if booking.status_reason == BookingStatusReason.PAYMENT_TIMEOUT:  # the sweeper got there first
        raise BookingExpired(booking_id=str(booking.id))
    if booking.status != BookingStatus.PENDING:
        raise BookingNotPayable(
            f"This booking is {booking.status}; only PENDING bookings can be paid.",
            booking_id=str(booking.id),
            booking_status=booking.status,
        )
    in_flight = booking.payments.filter(status=PaymentStatus.PENDING).first()
    if in_flight is not None:
        raise PaymentInProgress(payment_id=str(in_flight.id))


def _check_consistency(
    payment: Payment,
    result: PaymentResult,
    expected_amount: int | None,
    expected_currency: str | None,
) -> None:
    if (
        result.provider_payment_id
        and payment.provider_payment_id
        and result.provider_payment_id != payment.provider_payment_id
    ):
        raise ProviderResultRejected("provider_payment_id_mismatch")
    if expected_amount is not None and (expected_amount, expected_currency) != (
        payment.amount,
        payment.currency,
    ):
        raise ProviderResultRejected("amount_mismatch")


def _log_outcome(payment: Payment, outcome: Outcome, source: Source) -> None:
    fields = {
        "payment_id": str(payment.id),
        "booking_id": str(payment.booking_id),
        "status": payment.status,
        "source": source,
    }
    if outcome == Outcome.REFUND_OWED:
        # Money arrived for a booking we can't honour: operations must refund it.
        log.error("payment.refund_owed", reason="paid_after_booking_closed", **fields)
    elif outcome == Outcome.STALE:
        log.warning("payment.stale_result_ignored", **fields)
    else:
        log.info(f"payment.result_{outcome}", **fields)
