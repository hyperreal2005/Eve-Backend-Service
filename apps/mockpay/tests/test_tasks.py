"""MockPay's delivery semantics: at-least-once, retried with backoff, never redirected."""

import httpx
import pytest
import respx
from django.conf import settings
from django.utils import timezone

from apps.core.standard_webhooks import verify
from apps.mockpay import tasks
from apps.mockpay.models import ChargeStatus, MockCharge
from apps.mockpay.services import announce, charge_settle
from apps.mockpay.tasks import deliver_event

BODY = '{"type":"payment.succeeded"}'


def deliver(*responses):
    """Run a delivery (retries included, eagerly) against a receiver answering `responses`."""
    with respx.mock:
        route = respx.post(settings.MOCKPAY_WEBHOOK_URL).mock(side_effect=list(responses))
        result = deliver_event.apply(args=("evt_1", BODY))
    return route, result


def test_a_delivery_is_retried_until_the_receiver_acknowledges_it():
    route, result = deliver(
        httpx.Response(503), httpx.ConnectError("connection refused"), httpx.Response(429),
        httpx.Response(200, json={"status": "processed"}),
    )  # fmt: skip
    assert result.get() == 200
    assert route.call_count == 4
    for call in route.calls:  # every attempt: the same event, each one validly signed
        request = call.request
        assert request.headers["webhook-id"] == "evt_1"
        assert request.content == BODY.encode()
        verify(
            request.headers,
            request.content,
            secrets=[settings.MOCKPAY_WEBHOOK_SECRET],
            now=int(timezone.now().timestamp()),
            tolerance_seconds=300,
        )


@pytest.mark.parametrize("status", [400, 401, 404])
def test_a_refusal_is_not_retried(status):
    route, result = deliver(httpx.Response(status))
    assert (result.get(), route.call_count) == (status, 1)


def test_delivery_gives_up_after_the_last_attempt():
    route, result = deliver(*[httpx.Response(500)] * settings.MOCKPAY_DELIVERY_MAX_ATTEMPTS)
    assert route.call_count == settings.MOCKPAY_DELIVERY_MAX_ATTEMPTS
    with pytest.raises(tasks.RetryableDeliveryError):
        result.get()


# -------------------------------------------------------------------------- announcing


def make_charge(status: ChargeStatus, pending_outcome: str = "") -> MockCharge:
    return MockCharge.objects.create(
        reference="00000000-0000-0000-0000-000000000001",
        provider_payment_id="mockpay_pay_1",
        amount=100,
        currency="INR",
        method="mock_async_success",
        status=status,
        pending_outcome=pending_outcome,
    )


@pytest.fixture
def charge(db) -> MockCharge:
    return make_charge(ChargeStatus.SUCCEEDED)


@pytest.fixture
def queued(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        tasks.deliver_event, "apply_async", lambda args, **options: calls.append(args)
    )
    return calls


@pytest.mark.parametrize(("duplicate_rate", "deliveries"), [(0.0, 1), (1.0, 2)])
def test_a_result_is_announced_once_or_sometimes_twice(
    settings, charge, queued, duplicate_rate, deliveries
):
    settings.MOCKPAY_WEBHOOKS_ENABLED = True
    settings.MOCKPAY_DUPLICATE_DELIVERY_RATE = duplicate_rate
    announce(charge)
    assert len(queued) == deliveries
    assert len({event_id for event_id, _ in queued}) == 1  # duplicates share the event id


def test_nothing_is_announced_when_disabled_or_unsettled(settings, charge, queued):
    settings.MOCKPAY_WEBHOOKS_ENABLED = False
    announce(charge)
    settings.MOCKPAY_WEBHOOKS_ENABLED = True
    charge.status = ChargeStatus.PROCESSING
    announce(charge)
    assert queued == []


def test_a_broker_outage_is_logged_not_raised(settings, charge, monkeypatch):
    settings.MOCKPAY_WEBHOOKS_ENABLED = True

    def broker_down(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(tasks.deliver_event, "apply_async", broker_down)
    announce(charge)  # no exception: reconciliation will catch up


def test_settling_announces_the_result_after_commit(
    settings, db, queued, django_capture_on_commit_callbacks
):
    settings.MOCKPAY_WEBHOOKS_ENABLED = True
    settings.MOCKPAY_DUPLICATE_DELIVERY_RATE = 0.0
    processing = make_charge(ChargeStatus.PROCESSING, pending_outcome=ChargeStatus.SUCCEEDED)
    with django_capture_on_commit_callbacks(execute=True):
        charge_settle(charge_id=processing.id)
    assert len(queued) == 1
