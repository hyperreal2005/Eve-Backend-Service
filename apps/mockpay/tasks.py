"""MockPay's background work: settling asynchronous charges and delivering webhooks."""

import httpx
import structlog
from celery import Task, shared_task
from django.conf import settings

from apps.mockpay.events import deliver
from apps.mockpay.services import charge_settle

log = structlog.get_logger(__name__)

# Worth retrying: the receiver is overloaded, timing out or temporarily broken. Any other 4xx
# is a refusal that repeating the same request won't change.
_RETRYABLE_STATUSES = frozenset({408, 429})


class RetryableDeliveryError(Exception):
    pass


@shared_task(
    name="mockpay.deliver_event",
    bind=True,
    autoretry_for=(RetryableDeliveryError,),
    retry_backoff=2,  # 2s, 4s, 8s, ... with jitter, capped below
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=settings.MOCKPAY_DELIVERY_MAX_ATTEMPTS - 1,
)
def deliver_event(self: Task, event_id: str, body: str) -> int:
    """Deliver one event until the merchant acknowledges it (at-least-once).

    Each attempt is signed afresh, so a late retry isn't rejected as a replay; the event id and
    body never change, so the receiver can deduplicate.
    """
    attempt = self.request.retries + 1
    try:
        response = deliver(
            event_id=event_id,
            body=body.encode(),
            url=settings.MOCKPAY_WEBHOOK_URL,
            secret=settings.MOCKPAY_WEBHOOK_SECRET,
        )
    except httpx.TransportError as exc:
        log.warning(
            "mockpay.delivery_failed", event_id=event_id, attempt=attempt, error=type(exc).__name__
        )
        raise RetryableDeliveryError(str(exc)) from exc

    if response.status_code >= 500 or response.status_code in _RETRYABLE_STATUSES:
        log.warning(
            "mockpay.delivery_failed",
            event_id=event_id,
            attempt=attempt,
            status=response.status_code,
        )
        raise RetryableDeliveryError(f"HTTP {response.status_code}")
    if not response.is_success:
        log.error("mockpay.delivery_refused", event_id=event_id, status=response.status_code)
        return response.status_code
    log.info(
        "mockpay.delivered",
        event_id=event_id,
        attempt=attempt,
        receipt=response.json().get("status"),
    )
    return response.status_code


@shared_task(name="mockpay.settle_charge")
def settle_charge(charge_id: int) -> None:
    charge_settle(charge_id=charge_id)
