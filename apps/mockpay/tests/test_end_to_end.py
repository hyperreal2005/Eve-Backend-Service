"""The whole asynchronous chain, as it runs in the stack but with tasks executed eagerly:

pay (UPI-style) -> PENDING -> MockPay settles -> signed webhook over HTTP, delivered twice
-> booking CONFIRMED exactly once.
"""

import pytest

from apps.bookings.models import BookingStatusEvent
from apps.bookings.tests.factories import BookingFactory
from apps.payments.models import PaymentStatus, WebhookEvent
from apps.payments.services import payment_initiate

pytestmark = pytest.mark.django_db(transaction=True)


def test_an_asynchronous_payment_settles_itself_end_to_end(live_server, settings, eager_celery):
    settings.MOCKPAY_WEBHOOKS_ENABLED = True
    settings.MOCKPAY_DUPLICATE_DELIVERY_RATE = 1.0  # every event delivered twice
    # 127.0.0.1, not localhost: skip the IPv6 attempt Windows makes first.
    host = live_server.url.replace("localhost", "127.0.0.1")
    settings.MOCKPAY_WEBHOOK_URL = f"{host}/api/v1/payments/webhook/"
    booking = BookingFactory()

    payment = payment_initiate(
        user=booking.user, booking_id=booking.id, method="mock_async_success"
    )

    payment.refresh_from_db()
    booking.refresh_from_db()
    assert (payment.status, booking.status) == (PaymentStatus.SUCCESS, "CONFIRMED")
    assert list(WebhookEvent.objects.values_list("status", "outcome")) == [("PROCESSED", "applied")]
    confirmations = BookingStatusEvent.objects.filter(booking=booking, to_status="CONFIRMED")
    assert confirmations.count() == 1
