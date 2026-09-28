import json
import uuid
from typing import Any

import factory
from django.conf import settings
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.bookings.tests.factories import BookingFactory
from apps.core.standard_webhooks import signed_headers
from apps.payments.models import Payment, PaymentStatus

WEBHOOK_URL = "/api/v1/payments/webhook/"
PAYMENTS_URL = "/api/v1/payments/"


class PaymentFactory(DjangoModelFactory):
    class Meta:
        model = Payment

    booking = factory.SubFactory(BookingFactory)
    amount = factory.LazyAttribute(lambda payment: payment.booking.amount)
    currency = "INR"
    method = "mock_async_success"
    provider = "mockpay"
    provider_payment_id = factory.Sequence(lambda n: f"mockpay_pay_{n:024d}")
    status = PaymentStatus.PENDING
    completed_at = factory.LazyAttribute(
        lambda payment: None if payment.status == PaymentStatus.PENDING else timezone.now()
    )


def event_body(
    payment: Payment,
    *,
    event_type: str = "payment.succeeded",
    amount: int | None = None,
    **data: Any,
) -> bytes:
    """A MockPay-style event about `payment`, as raw bytes."""
    body = {
        "type": event_type,
        "timestamp": timezone.now().isoformat(),
        "data": {
            "payment_id": str(payment.id),
            "provider_payment_id": payment.provider_payment_id,
            "amount": payment.amount if amount is None else amount,
            "currency": payment.currency,
            "failure_code": "card_declined" if event_type == "payment.failed" else None,
            **data,
        },
    }
    return json.dumps(body).encode()


def post_webhook(
    client: Any,
    body: bytes,
    *,
    event_id: str | None = None,
    secret: str | None = None,
    timestamp: int | None = None,
    url: str = WEBHOOK_URL,
    **extra_headers: str,
) -> Any:
    """Deliver `body` exactly as the provider would: signed, with the Standard Webhooks headers."""
    headers = signed_headers(
        secret or settings.WEBHOOK_SECRETS[0],
        msg_id=event_id or f"evt_{uuid.uuid4().hex}",
        timestamp=timestamp or int(timezone.now().timestamp()),
        body=body,
    )
    return client.post(
        url, data=body, content_type="application/json", headers={**headers, **extra_headers}
    )
