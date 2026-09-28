"""Payment races that only a real database can arbitrate. Each thread has its own connection."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import bearer
from apps.bookings.models import BookingStatusEvent
from apps.bookings.tests.factories import BookingFactory
from apps.core.tests.concurrency import run_concurrently
from apps.mockpay.models import MockCharge
from apps.payments.gateway import PaymentResult
from apps.payments.models import Payment, PaymentStatus, WebhookEvent
from apps.payments.services import Source, payment_apply_result
from apps.payments.tests.factories import PAYMENTS_URL, PaymentFactory, event_body, post_webhook

pytestmark = pytest.mark.django_db(transaction=True)


def confirmations(payment: Payment) -> int:
    return BookingStatusEvent.objects.filter(booking=payment.booking, to_status="CONFIRMED").count()


def test_the_same_webhook_delivered_eight_times_at_once_is_applied_once():
    payment = PaymentFactory()
    body = event_body(payment)

    responses = run_concurrently(
        8, lambda _: post_webhook(APIClient(), body, event_id="evt_race").json()
    )

    assert sorted(r["status"] for r in responses) == ["duplicate"] * 7 + ["processed"]
    assert WebhookEvent.objects.count() == 1
    assert confirmations(payment) == 1


def test_webhooks_and_reconciliation_racing_on_one_payment_confirm_it_once():
    """Different event ids and a reconciliation pass all report SUCCESS at the same moment."""
    payment = PaymentFactory()

    def report(index: int) -> None:
        if index % 2:
            post_webhook(APIClient(), event_body(payment), event_id=f"evt_{index}")
        else:
            payment_apply_result(
                payment_id=payment.id,
                result=PaymentResult(status=PaymentStatus.SUCCESS),
                source=Source.RECONCILIATION,
            )

    run_concurrently(8, report)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS
    assert confirmations(payment) == 1


def test_eight_simultaneous_pay_clicks_charge_once():
    booking = BookingFactory()
    header = bearer(booking.user)

    def click(_: int) -> int:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=header)
        payload = {"booking_id": str(booking.id), "payment_method": "mock_success"}
        return client.post(PAYMENTS_URL, payload, format="json").status_code

    statuses = run_concurrently(8, click)

    assert sorted(statuses) == [201] + [409] * 7
    assert Payment.objects.count() == 1
    assert MockCharge.objects.count() == 1
