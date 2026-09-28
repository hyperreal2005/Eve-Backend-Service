"""What a payment result means for the payment and its booking: one pure, total function.

Every path that learns a result (the gateway's response, the webhook, reconciliation) goes
through `decide`, so they agree by construction and arrive at the same state in any order.
"""

from dataclasses import dataclass
from enum import StrEnum

from apps.bookings.models import BookingStatus
from apps.payments.models import PaymentStatus


class Outcome(StrEnum):
    APPLIED = "applied"  # the payment moved, and the booking with it where it was waiting
    NOOP = "noop"  # nothing new: a duplicate, or the other path got here first
    REFUND_OWED = "refund_owed"  # money arrived for a booking that can't be honoured any more
    STALE = "stale_event"  # a result that contradicts a captured payment; ignored


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    payment_to: PaymentStatus | None = None
    booking_to: BookingStatus | None = None
    refund_owed: bool = False


def decide(payment: str, incoming: str, booking: str) -> Decision:
    """Given the payment's status, the reported result and the booking's status, what changes?

    - The same result again (or "still processing") changes nothing.
    - SUCCESS is final: money was captured, so a later FAILED is stale and ignored.
    - SUCCESS may follow FAILED: banks authorise late (Razorpay documents this). The money is
      real, so the payment records it; if the booking can no longer be honoured, a refund is owed
      instead of resurrecting a booking that already failed or was cancelled.
    """
    if incoming == PaymentStatus.PENDING or incoming == payment:
        return Decision(Outcome.NOOP)
    if payment == PaymentStatus.SUCCESS:
        return Decision(Outcome.STALE)
    if incoming == PaymentStatus.SUCCESS:
        if booking == BookingStatus.PENDING:
            return Decision(
                Outcome.APPLIED,
                payment_to=PaymentStatus.SUCCESS,
                booking_to=BookingStatus.CONFIRMED,
            )
        return Decision(Outcome.REFUND_OWED, payment_to=PaymentStatus.SUCCESS, refund_owed=True)
    # incoming FAILED, payment PENDING
    return Decision(
        Outcome.APPLIED,
        payment_to=PaymentStatus.FAILED,
        booking_to=BookingStatus.FAILED if booking == BookingStatus.PENDING else None,
    )
