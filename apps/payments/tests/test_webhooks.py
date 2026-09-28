"""The webhook over HTTP: authenticity, idempotency, ordering and failure handling."""

import time

import pytest
from structlog.testing import capture_logs

from apps.bookings.models import BookingStatus
from apps.core.tests.assertions import assert_problem
from apps.payments import services
from apps.payments.models import PaymentStatus, WebhookEvent
from apps.payments.tests.factories import WEBHOOK_URL, PaymentFactory, event_body, post_webhook

pytestmark = pytest.mark.django_db

OTHER_SECRET = "whsec_b3RoZXItc2VjcmV0LWZvci1yb3RhdGlvbg=="


def receipt(response) -> tuple[str, str]:
    assert response.status_code == 200, response.content
    return response.json()["status"], response.json()["outcome"]


def statuses(payment) -> tuple[str, str]:
    payment.refresh_from_db()
    payment.booking.refresh_from_db()
    return payment.status, payment.booking.status


# ------------------------------------------------------------------------ applying events


def test_a_success_event_confirms_the_booking(api_client):
    payment = PaymentFactory()
    response = post_webhook(api_client, event_body(payment), event_id="evt_1")

    assert receipt(response) == ("processed", "applied")
    assert statuses(payment) == ("SUCCESS", "CONFIRMED")
    event = WebhookEvent.objects.get()
    assert (event.event_id, event.status, event.outcome, event.payment_id) == (
        "evt_1",
        "PROCESSED",
        "applied",
        payment.id,
    )


def test_a_failure_event_fails_the_booking(api_client):
    payment = PaymentFactory()
    response = post_webhook(api_client, event_body(payment, event_type="payment.failed"))
    assert receipt(response) == ("processed", "applied")
    assert statuses(payment) == ("FAILED", "FAILED")


def test_the_same_event_delivered_again_changes_nothing(api_client):
    payment = PaymentFactory()
    body = event_body(payment)
    responses = [post_webhook(api_client, body, event_id="evt_same") for _ in range(3)]

    assert [receipt(r)[0] for r in responses] == ["processed", "duplicate", "duplicate"]
    assert WebhookEvent.objects.count() == 1
    assert payment.booking.status_events.filter(to_status="CONFIRMED").count() == 1


def test_a_reused_event_id_with_different_content_is_flagged_but_not_applied(api_client):
    payment = PaymentFactory()
    post_webhook(api_client, event_body(payment), event_id="evt_x")
    with capture_logs() as logs:
        response = post_webhook(
            api_client, event_body(payment, event_type="payment.failed"), event_id="evt_x"
        )
    assert receipt(response)[0] == "duplicate"
    assert statuses(payment) == ("SUCCESS", "CONFIRMED")
    assert any(entry["event"] == "webhook.duplicate_payload_mismatch" for entry in logs)


def test_a_new_event_reporting_an_already_known_result_is_a_no_op(api_client):
    payment = PaymentFactory()
    post_webhook(api_client, event_body(payment))
    assert receipt(post_webhook(api_client, event_body(payment))) == ("processed", "noop")


def test_a_failure_arriving_after_success_is_ignored(api_client):
    payment = PaymentFactory()
    post_webhook(api_client, event_body(payment))
    response = post_webhook(api_client, event_body(payment, event_type="payment.failed"))
    assert receipt(response) == ("ignored", "stale_event")
    assert statuses(payment) == ("SUCCESS", "CONFIRMED")


def test_money_for_a_closed_booking_is_recorded_and_flagged_for_refund(api_client):
    payment = PaymentFactory(status=PaymentStatus.FAILED)
    payment.booking.status = BookingStatus.FAILED
    payment.booking.save()

    assert receipt(post_webhook(api_client, event_body(payment))) == ("processed", "refund_owed")
    payment.refresh_from_db()
    assert (payment.status, payment.refund_status) == ("SUCCESS", "PENDING")


def test_unsupported_event_types_are_acknowledged_and_ignored(api_client):
    payment = PaymentFactory()
    response = post_webhook(api_client, event_body(payment, event_type="payment.refunded"))
    assert receipt(response) == ("ignored", "unsupported_event_type")
    assert statuses(payment) == ("PENDING", "PENDING")


# ------------------------------------------------ authentic events we can't apply: dead-letter


def test_an_event_for_an_unknown_payment_is_kept_for_review(api_client):
    payment = PaymentFactory()
    body = event_body(payment, payment_id="00000000-0000-0000-0000-000000000000")
    assert receipt(post_webhook(api_client, body)) == ("rejected", "unknown_payment")
    event = WebhookEvent.objects.get()
    assert (event.status, event.payment_id) == ("REJECTED", None)


def test_an_event_with_the_wrong_amount_is_kept_for_review_and_not_applied(api_client):
    payment = PaymentFactory()
    response = post_webhook(api_client, event_body(payment, amount=payment.amount + 1))
    assert receipt(response) == ("rejected", "amount_mismatch")
    assert statuses(payment) == ("PENDING", "PENDING")


# ----------------------------------------------------------------------- authenticity


def test_an_unsigned_request_is_refused_and_not_stored(api_client):
    payment = PaymentFactory()
    response = api_client.post(
        WEBHOOK_URL, data=event_body(payment), content_type="application/json"
    )
    assert_problem(response, 401, "WEBHOOK_SIGNATURE_INVALID")
    assert response["WWW-Authenticate"].startswith("Standard-Webhooks")
    assert not WebhookEvent.objects.exists()
    assert statuses(payment) == ("PENDING", "PENDING")


def test_a_request_signed_with_the_wrong_secret_is_refused(api_client):
    response = post_webhook(api_client, event_body(PaymentFactory()), secret=OTHER_SECRET)
    assert_problem(response, 401, "WEBHOOK_SIGNATURE_INVALID")


def test_a_replayed_old_request_is_refused(api_client):
    ten_minutes_ago = int(time.time()) - 600
    response = post_webhook(api_client, event_body(PaymentFactory()), timestamp=ten_minutes_ago)
    assert_problem(response, 401, "WEBHOOK_SIGNATURE_INVALID")


def test_the_previous_secret_is_still_accepted_during_rotation(api_client, settings):
    settings.WEBHOOK_SECRETS = [OTHER_SECRET, settings.WEBHOOK_SECRETS[0]]
    old_secret = settings.WEBHOOK_SECRETS[1]
    response = post_webhook(api_client, event_body(PaymentFactory()), secret=old_secret)
    assert receipt(response)[0] == "processed"


def test_a_user_token_is_neither_needed_nor_looked_at(api_client):
    response = post_webhook(
        api_client, event_body(PaymentFactory()), Authorization="Bearer not-a-real-token"
    )
    assert receipt(response)[0] == "processed"


# ---------------------------------------------------------------------- malformed requests


@pytest.mark.parametrize(
    "body",
    [b"{not json", b"[]", b'{"type": "payment.succeeded"}', b'{"type": "payment.succeeded", '
     b'"timestamp": "2026-01-01T00:00:00Z", "data": {"payment_id": "nope"}}'],
)  # fmt: skip
def test_a_signed_but_malformed_body_is_refused(api_client, body):
    assert_problem(post_webhook(api_client, body), 400, "WEBHOOK_PAYLOAD_INVALID")
    assert not WebhookEvent.objects.exists()


def test_a_non_json_body_is_refused(api_client):
    response = api_client.post(WEBHOOK_URL, data={"a": "b"}, format="multipart")
    assert_problem(response, 415, "UNSUPPORTED_MEDIA_TYPE")


# ------------------------------------------------------------------------------- routing


@pytest.mark.parametrize(
    "url", ["/payments/webhook/", "/api/v1/payments/webhook", "/payments/webhook"]
)
def test_the_briefs_literal_path_and_slashless_forms_work_without_redirects(api_client, url):
    assert (
        receipt(post_webhook(api_client, event_body(PaymentFactory()), url=url))[0] == "processed"
    )


# ------------------------------------------------------------------------ crash safety


def test_a_crash_mid_processing_leaves_no_trace_so_the_retry_succeeds(api_client, monkeypatch):
    """Recording an event and applying it share one transaction: never one without the other."""
    payment = PaymentFactory()
    body = event_body(payment)

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash after the event was recorded")

    monkeypatch.setattr(services, "booking_transition", crash)
    assert_problem(post_webhook(api_client, body, event_id="evt_crash"), 500, "INTERNAL_ERROR")
    assert not WebhookEvent.objects.exists()
    assert statuses(payment) == ("PENDING", "PENDING")

    monkeypatch.undo()  # the provider retries the same event
    assert receipt(post_webhook(api_client, body, event_id="evt_crash")) == ("processed", "applied")
    assert statuses(payment) == ("SUCCESS", "CONFIRMED")
