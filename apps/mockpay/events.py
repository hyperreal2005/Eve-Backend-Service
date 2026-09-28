"""Building and delivering MockPay's webhook events (the provider side of Standard Webhooks)."""

import json
import time
import uuid
from functools import cache

import httpx
from django.utils import timezone

from apps.core import standard_webhooks
from apps.mockpay.models import ChargeStatus, MockCharge

EVENT_TYPES = {
    ChargeStatus.SUCCEEDED: "payment.succeeded",
    ChargeStatus.FAILED: "payment.failed",
}


def build_event(
    charge: MockCharge,
    *,
    status: ChargeStatus | None = None,
    amount: int | None = None,
    event_id: str | None = None,
) -> tuple[str, bytes]:
    """An event id and its JSON body. The body is fixed once built; only signatures vary."""
    status = status or ChargeStatus(charge.status)
    body = {
        "type": EVENT_TYPES[status],
        "timestamp": timezone.now().isoformat(),
        "data": {
            "payment_id": str(charge.reference),
            "provider_payment_id": charge.provider_payment_id,
            "amount": charge.amount if amount is None else amount,
            "currency": charge.currency,
            "failure_code": (charge.failure_code or "declined")
            if status == ChargeStatus.FAILED
            else None,
        },
    }
    event_id = event_id or f"evt_{uuid.uuid4().hex}"
    return event_id, json.dumps(body, separators=(",", ":")).encode()


@cache
def _client() -> httpx.Client:
    # One pooled client per process (created lazily, so after a worker forks): no new TLS
    # context and connection for every delivery.
    return httpx.Client(timeout=10.0)


def close_http_client() -> None:
    """Close the pooled connections (at shutdown, or between tests)."""
    if _client.cache_info().currsize:
        _client().close()
        _client.cache_clear()


def deliver(*, event_id: str, body: bytes, url: str, secret: str) -> httpx.Response:
    """POST one delivery attempt, signed afresh (a retried event gets a new timestamp)."""
    headers = {
        "content-type": "application/json",
        **standard_webhooks.signed_headers(
            secret, msg_id=event_id, timestamp=int(time.time()), body=body
        ),
    }
    return _client().post(url, content=body, headers=headers)
