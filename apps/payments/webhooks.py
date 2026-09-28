"""Receiving provider events.

1. Authenticate: Standard Webhooks HMAC over the raw body, with a replay window. Failing that,
   refuse (401) and store nothing: it may be forged.
2. Parse: a malformed body is refused (400).
3. In one transaction, claim the event id and apply the event. The unique (provider, event_id)
   index makes a concurrent duplicate wait, then see the claim. Claiming and applying share the
   transaction, so an event can't be recorded without its effect, or applied twice.

An authentic event is always acknowledged, even one we can't apply (unknown payment, amount
mismatch): it is stored as REJECTED for review. Retrying can't fix it, and providers disable
endpoints that keep failing. Only transient failures answer 5xx, so the provider retries.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.core import standard_webhooks
from apps.core.db import retry_on_transient_errors
from apps.payments.domain import Outcome
from apps.payments.errors import (
    ProviderResultRejected,
    WebhookPayloadInvalid,
    WebhookSignatureInvalid,
)
from apps.payments.gateway import PaymentResult, get_gateway
from apps.payments.models import PaymentStatus, WebhookEvent, WebhookEventStatus
from apps.payments.services import Source, payment_apply_result

log = structlog.get_logger(__name__)

SUPPORTED_EVENTS = {
    "payment.succeeded": PaymentStatus.SUCCESS,
    "payment.failed": PaymentStatus.FAILED,
}


class _EnvelopeSerializer(serializers.Serializer):
    type = serializers.CharField(max_length=64)
    timestamp = serializers.DateTimeField()
    data = serializers.DictField()


class _PaymentDataSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField()
    provider_payment_id = serializers.CharField(max_length=64)
    amount = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(min_length=3, max_length=3)
    failure_code = serializers.CharField(max_length=64, required=False, allow_null=True)
    failure_message = serializers.CharField(max_length=255, required=False, allow_null=True)


@dataclass(frozen=True)
class ProviderEvent:
    id: str
    type: str
    payload: dict[str, Any]
    # Set for supported payment events; None means "authentic, but not a type we act on".
    payment_id: UUID | None = None
    amount: int | None = None
    currency: str | None = None
    result: PaymentResult | None = None


@dataclass(frozen=True)
class WebhookReceipt:
    event_id: str
    status: str  # processed | duplicate | ignored | rejected
    outcome: str


def webhook_receive(*, headers: Mapping[str, str], body: bytes) -> WebhookReceipt:
    try:
        event_id = standard_webhooks.verify(
            headers,
            body,
            secrets=settings.WEBHOOK_SECRETS,
            now=int(timezone.now().timestamp()),
            tolerance_seconds=settings.WEBHOOK_TOLERANCE_SECONDS,
        )
    except standard_webhooks.SignatureError as exc:
        log.warning("webhook.signature_invalid", reason=str(exc))
        raise WebhookSignatureInvalid() from exc

    event = parse_event(event_id, body)
    digest = hashlib.sha256(body).hexdigest()
    log.info("webhook.received", event_id=event.id, event_type=event.type)
    return retry_on_transient_errors(lambda: _record_and_apply(event, digest))


def parse_event(event_id: str, body: bytes) -> ProviderEvent:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookPayloadInvalid("The body is not valid JSON.") from exc
    envelope = _EnvelopeSerializer(data=payload)
    if not envelope.is_valid():
        raise WebhookPayloadInvalid(f"Invalid event envelope: {envelope.errors}")
    event_type = envelope.validated_data["type"]
    status = SUPPORTED_EVENTS.get(event_type)
    if status is None:
        return ProviderEvent(id=event_id, type=event_type, payload=payload)

    data = _PaymentDataSerializer(data=envelope.validated_data["data"])
    if not data.is_valid():
        raise WebhookPayloadInvalid(f"Invalid {event_type} data: {data.errors}")
    fields = data.validated_data
    return ProviderEvent(
        id=event_id,
        type=event_type,
        payload=payload,
        payment_id=fields["payment_id"],
        amount=fields["amount"],
        currency=fields["currency"],
        result=PaymentResult(
            status=status,
            provider_payment_id=fields["provider_payment_id"],
            failure_code=fields.get("failure_code") or "",
            failure_message=fields.get("failure_message") or "",
        ),
    )


def _record_and_apply(event: ProviderEvent, digest: str) -> WebhookReceipt:
    with transaction.atomic():
        record, created = WebhookEvent.objects.get_or_create(
            provider=get_gateway().name,
            event_id=event.id,
            defaults={
                "event_type": event.type,
                "payload": event.payload,
                "payload_sha256": digest,
                "status": WebhookEventStatus.PROCESSED,
            },
        )
        if not created:
            if record.payload_sha256 != digest:
                # Same id, different content: a provider bug or tampering. The first one stands.
                log.error("webhook.duplicate_payload_mismatch", event_id=event.id)
            log.info("webhook.duplicate", event_id=event.id, first_status=record.status)
            return WebhookReceipt(event_id=event.id, status="duplicate", outcome=record.outcome)

        status, outcome, payment_id = _apply(event)
        record.status, record.outcome, record.payment_id = status, outcome, payment_id
        record.save(update_fields=["status", "outcome", "payment"])
    return WebhookReceipt(event_id=event.id, status=status.lower(), outcome=outcome)


def _apply(event: ProviderEvent) -> tuple[WebhookEventStatus, str, UUID | None]:
    if event.result is None or event.payment_id is None:
        log.info("webhook.ignored", event_id=event.id, event_type=event.type)
        return WebhookEventStatus.IGNORED, "unsupported_event_type", None
    try:
        outcome = payment_apply_result(
            payment_id=event.payment_id,
            result=event.result,
            source=Source.WEBHOOK,
            expected_amount=event.amount,
            expected_currency=event.currency,
        )
    except ProviderResultRejected as exc:
        log.error("webhook.rejected", event_id=event.id, reason=exc.reason)
        known = exc.reason != "unknown_payment"
        return WebhookEventStatus.REJECTED, exc.reason, event.payment_id if known else None
    status = (
        WebhookEventStatus.IGNORED if outcome == Outcome.STALE else WebhookEventStatus.PROCESSED
    )
    return status, outcome, event.payment_id
