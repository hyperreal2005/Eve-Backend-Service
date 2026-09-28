"""`decide` over its whole input space: 3 payment states x 3 results x 4 booking states."""

from itertools import product

import pytest

from apps.bookings.domain import can_transition
from apps.bookings.models import BookingStatus
from apps.payments.domain import Decision, Outcome, decide
from apps.payments.models import PaymentStatus

PENDING, SUCCESS, FAILED = PaymentStatus.PENDING, PaymentStatus.SUCCESS, PaymentStatus.FAILED
WAITING, CONFIRMED, CLOSED_FAILED, CANCELLED = (
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.FAILED,
    BookingStatus.CANCELLED,
)
EVERY_INPUT = list(product(PaymentStatus, PaymentStatus, BookingStatus))
REFUND = Decision(Outcome.REFUND_OWED, SUCCESS, refund_owed=True)


@pytest.mark.parametrize(("payment", "incoming", "booking", "expected"), [
    # The ordinary paths.
    (PENDING, SUCCESS, WAITING, Decision(Outcome.APPLIED, SUCCESS, CONFIRMED)),
    (PENDING, FAILED, WAITING, Decision(Outcome.APPLIED, FAILED, CLOSED_FAILED)),
    # A duplicate, or the other path got there first.
    (SUCCESS, SUCCESS, CONFIRMED, Decision(Outcome.NOOP)),
    (FAILED, FAILED, CLOSED_FAILED, Decision(Outcome.NOOP)),
    # "Still processing" never changes anything.
    (PENDING, PENDING, WAITING, Decision(Outcome.NOOP)),
    # Out of order: FAILED after the money was captured is stale.
    (SUCCESS, FAILED, CONFIRMED, Decision(Outcome.STALE)),
    # Late authorisation: money arrived after the booking failed. Record it; refund it.
    (FAILED, SUCCESS, CLOSED_FAILED, REFUND),
    # Paid after the booking was cancelled.
    (PENDING, SUCCESS, CANCELLED, REFUND),
    # A decline for a booking that's already closed only updates the payment.
    (PENDING, FAILED, CANCELLED, Decision(Outcome.APPLIED, FAILED)),
])  # fmt: skip
def test_the_documented_cases(payment, incoming, booking, expected):
    assert decide(payment, incoming, booking) == expected


@pytest.mark.parametrize(("payment", "incoming", "booking"), EVERY_INPUT)
def test_the_rules_hold_for_every_input(payment, incoming, booking):
    decision = decide(payment, incoming, booking)

    # A captured payment is never undone.
    if payment == SUCCESS:
        assert decision.payment_to is None
    # Only a success confirms a booking, and only one that is waiting for it.
    if decision.booking_to == BookingStatus.CONFIRMED:
        assert (decision.payment_to, booking) == (SUCCESS, BookingStatus.PENDING)
    # Booking changes are always legal transitions (terminal states stay terminal).
    if decision.booking_to is not None:
        assert can_transition(booking, decision.booking_to)
    # A refund is owed exactly when money is taken for a booking that isn't waiting for it.
    assert decision.refund_owed == (
        decision.payment_to == SUCCESS and booking != BookingStatus.PENDING
    )
    # Nothing changes for "still processing" or a repeat of the current state.
    if incoming in (PENDING, payment):
        assert decision == Decision(Outcome.NOOP)
