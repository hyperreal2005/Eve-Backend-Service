"""The `send_webhook` command, end to end: signed events over real HTTP to a live server."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.bookings.tests.factories import BookingFactory
from apps.payments.models import PaymentStatus
from apps.payments.services import payment_initiate

pytestmark = pytest.mark.django_db(transaction=True)


def send(live_server, payment_id, *args: str) -> str:
    out = StringIO()
    host = live_server.url.replace("localhost", "127.0.0.1")  # skip Windows' IPv6 attempt
    url = f"{host}/api/v1/payments/webhook/"
    call_command("send_webhook", str(payment_id), "--url", url, *args, stdout=out)
    return out.getvalue()


def test_a_pending_payment_is_settled_by_a_delivered_event_and_duplicates_are_harmless(
    live_server,
):
    booking = BookingFactory()
    payment = payment_initiate(
        user=booking.user, booking_id=booking.id, method="mock_async_success"
    )

    output = send(live_server, payment.id, "--times", "2")

    assert '"status":"processed","outcome":"applied"' in output.replace(" ", "")
    assert '"status":"duplicate"' in output.replace(" ", "")
    payment.refresh_from_db()
    booking.refresh_from_db()
    assert (payment.status, booking.status) == (PaymentStatus.SUCCESS, "CONFIRMED")


def test_a_forged_signature_is_refused(live_server):
    booking = BookingFactory()
    payment = payment_initiate(
        user=booking.user, booking_id=booking.id, method="mock_async_success"
    )
    assert "HTTP 401" in send(live_server, payment.id, "--bad-signature")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING


def test_an_unknown_payment_is_reported_clearly(live_server):
    with pytest.raises(CommandError):
        send(live_server, "00000000-0000-0000-0000-000000000000")
