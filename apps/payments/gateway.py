"""The port between our payment logic and a payment provider.

`payments` only ever talks to a provider through this interface (outbound) and through signed
webhooks (inbound). MockPay implements it today; a Razorpay adapter would implement the same
three members, and nothing else in bookings or payments would change.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from django.conf import settings
from django.utils.module_loading import import_string

from apps.payments.models import PaymentStatus


@dataclass(frozen=True)
class PaymentResult:
    """A provider's verdict on one of our payments, in our vocabulary."""

    status: PaymentStatus  # PENDING means "still processing"
    provider_payment_id: str = ""
    failure_code: str = ""
    failure_message: str = ""


class GatewayError(Exception):
    """The provider couldn't be reached or answered unclearly: the outcome is unknown.

    Unknown is not failed. The payment stays PENDING until the webhook or reconciliation learns
    what actually happened.
    """


class PaymentGateway(Protocol):
    name: str
    payment_methods: Sequence[str]
    default_method: str

    def charge(self, *, reference: UUID, amount: int, currency: str, method: str) -> PaymentResult:
        """Charge once. Idempotent on `reference`: repeating it returns the original result."""
        ...

    def fetch(self, *, reference: UUID) -> PaymentResult | None:
        """The provider's current view of a charge, or None if it has no such charge."""
        ...


def get_gateway() -> PaymentGateway:
    return import_string(settings.PAYMENT_GATEWAY)()
