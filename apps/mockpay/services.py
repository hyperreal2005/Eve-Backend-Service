import random
import uuid
from typing import Any
from uuid import UUID

import structlog
from celery import Task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.mockpay.events import build_event
from apps.mockpay.models import ChargeStatus, MockCharge

log = structlog.get_logger(__name__)


def charge_create(
    *,
    reference: UUID,
    amount: int,
    currency: str,
    method: str,
    outcome: ChargeStatus,
    failure_code: str = "",
    asynchronous: bool = False,
) -> MockCharge:
    """Record a charge, once per reference: a repeated request returns the original charge, so
    a retried charge can never produce a second payment or a different outcome."""
    settled = not asynchronous
    with transaction.atomic():
        charge, created = MockCharge.objects.get_or_create(
            reference=reference,
            defaults={
                "provider_payment_id": f"mockpay_pay_{uuid.uuid4().hex[:24]}",
                "amount": amount,
                "currency": currency,
                "method": method,
                "status": outcome if settled else ChargeStatus.PROCESSING,
                "failure_code": failure_code if outcome == ChargeStatus.FAILED else "",
                "pending_outcome": "" if settled else outcome,
                "settled_at": timezone.now() if settled else None,
            },
        )
        if created:
            # After our own commit, like any provider: announce a final result, or settle later.
            follow_up = announce if settled else schedule_settlement
            transaction.on_commit(lambda: follow_up(charge), robust=True)
    if created:
        log.info(
            "mockpay.charge_created",
            provider_payment_id=charge.provider_payment_id,
            status=charge.status,
        )
    return charge


def charge_settle(*, charge_id: int) -> MockCharge | None:
    """Finish an asynchronous charge with the outcome chosen at charge time. Idempotent."""
    with transaction.atomic():
        charge = MockCharge.objects.select_for_update().filter(id=charge_id).first()
        if charge is None or charge.status != ChargeStatus.PROCESSING:
            return charge
        charge.status = charge.pending_outcome
        charge.settled_at = timezone.now()
        charge.save(update_fields=["status", "settled_at"])
        transaction.on_commit(lambda: announce(charge), robust=True)
    log.info(
        "mockpay.charge_settled",
        provider_payment_id=charge.provider_payment_id,
        status=charge.status,
    )
    return charge


def announce(charge: MockCharge) -> None:
    """Push the charge's result to the merchant by webhook: after a delay, at least once, and
    now and then twice on purpose, because real providers do and receivers must cope."""
    if not settings.MOCKPAY_WEBHOOKS_ENABLED or charge.status == ChargeStatus.PROCESSING:
        return  # disabled, or nothing to announce until the charge settles
    from apps.mockpay.tasks import deliver_event  # the tasks module imports this one

    event_id, body = build_event(charge)
    deliveries = 1 + (random.random() < settings.MOCKPAY_DUPLICATE_DELIVERY_RATE)  # noqa: S311
    for extra_delay in range(deliveries):
        _enqueue(
            deliver_event,
            args=(event_id, body.decode()),
            countdown=settings.MOCKPAY_WEBHOOK_DELAY_SECONDS + extra_delay,
        )


def schedule_settlement(charge: MockCharge) -> None:
    if not settings.MOCKPAY_WEBHOOKS_ENABLED:
        return
    from apps.mockpay.tasks import settle_charge

    _enqueue(settle_charge, args=(charge.id,), countdown=settings.MOCKPAY_ASYNC_SETTLE_SECONDS)


def _enqueue(task: Task, **options: Any) -> None:
    """Queue a task, best effort: if the broker is down, the merchant's reconciliation job still
    learns the outcome by asking, so this is logged rather than raised."""
    # The originating request's id, from an HTTP request or a task it queued, so one id traces
    # the whole chain: request -> settlement -> webhook delivery.
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    try:
        task.apply_async(headers={"request_id": request_id} if request_id else None, **options)
    except Exception as exc:
        log.warning("mockpay.enqueue_failed", task=task.name, error=type(exc).__name__)
