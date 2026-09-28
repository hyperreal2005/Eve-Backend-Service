from typing import ClassVar

from apps.core.errors import Conflict, DomainError, NotFound


class PaymentNotFound(NotFound):
    code = "PAYMENT_NOT_FOUND"
    title = "Payment not found"
    default_detail = "No payment with this id."


class BookingAlreadyPaid(Conflict):
    code = "BOOKING_ALREADY_PAID"
    title = "Booking already paid"
    default_detail = "This booking is already confirmed and paid for."


class BookingNotPayable(Conflict):
    code = "BOOKING_NOT_PAYABLE"
    title = "Booking is not payable"
    default_detail = "Only PENDING bookings can be paid."


class BookingExpired(Conflict):
    code = "BOOKING_EXPIRED"
    title = "Booking hold expired"
    default_detail = "The booking wasn't paid in time and has been released. Please book again."


class PaymentInProgress(Conflict):
    code = "PAYMENT_IN_PROGRESS"
    title = "Payment in progress"
    default_detail = "A payment for this booking is already being processed."


class WebhookSignatureInvalid(DomainError):
    status_code = 401
    code = "WEBHOOK_SIGNATURE_INVALID"
    title = "Invalid webhook signature"
    default_detail = "The request is not signed by the payment provider, or is too old."
    www_authenticate: ClassVar[str] = 'Standard-Webhooks realm="payment-provider"'


class WebhookPayloadInvalid(DomainError):
    code = "WEBHOOK_PAYLOAD_INVALID"
    title = "Invalid webhook payload"
    default_detail = "The webhook body is not a valid provider event."


class ProviderResultRejected(Exception):
    """A signed provider result that we can't apply: the event is kept for review, not retried."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
