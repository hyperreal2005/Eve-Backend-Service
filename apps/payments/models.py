from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.bookings.models import Booking
from apps.core.models import BaseModel


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"


class RefundStatus(models.TextChoices):
    # Recorded, not executed: a refund engine is out of scope (see the README).
    PENDING = "PENDING", "Refund owed"
    PROCESSED = "PROCESSED", "Refunded"


LIVE_STATUSES = (PaymentStatus.PENDING, PaymentStatus.SUCCESS)


class Payment(BaseModel):
    """One attempt to pay for a booking.

    The id doubles as our reference at the provider, so the provider can tell us about a payment
    even if we crashed before storing the provider's own id.
    """

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="payments")
    amount = models.IntegerField(help_text="In paise; always the booking's amount.")
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    method = models.CharField(max_length=64)
    provider = models.CharField(max_length=32)
    provider_payment_id = models.CharField(max_length=64, blank=True, default="")
    failure_code = models.CharField(max_length=64, blank=True, default="")
    failure_message = models.CharField(max_length=255, blank=True, default="")
    refund_status = models.CharField(
        max_length=16, choices=RefundStatus.choices, blank=True, default=""
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    # From the creating request's Idempotency-Key header, if it sent one.
    idempotency_key = models.CharField(max_length=255, blank=True, default="")
    request_fingerprint = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "payments"
        constraints = (
            # At most one in-flight or successful payment per booking: no double charge, even
            # when two "Pay" clicks race. Failed attempts don't count.
            models.UniqueConstraint(
                fields=("booking",),
                condition=Q(status__in=LIVE_STATUSES),
                name="payments_one_live_per_booking",
            ),
            # The key leads: a replay looks the key up first, then checks the patient.
            models.UniqueConstraint(
                fields=("idempotency_key", "booking"),
                condition=~Q(idempotency_key=""),
                name="payments_idempotency_key_uniq",
            ),
            models.UniqueConstraint(
                fields=("provider", "provider_payment_id"),
                condition=~Q(provider_payment_id=""),
                name="payments_provider_payment_id_uniq",
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payments_amount_positive"),
            models.CheckConstraint(
                condition=Q(status__in=PaymentStatus.values), name="payments_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(status=PaymentStatus.PENDING) | Q(completed_at__isnull=False),
                name="payments_completed_has_timestamp",
            ),
            # Only money we actually took can be owed back.
            models.CheckConstraint(
                condition=Q(refund_status="")
                | Q(refund_status__in=RefundStatus.values, status=PaymentStatus.SUCCESS),
                name="payments_refund_only_when_paid",
            ),
        )
        indexes = (
            # The reconciliation job's scan: payments still waiting for a result.
            models.Index(
                fields=("created_at",),
                condition=Q(status=PaymentStatus.PENDING),
                name="payments_pending_idx",
            ),
        )

    def __str__(self) -> str:
        return f"Payment {self.id} ({self.status})"


class WebhookEventStatus(models.TextChoices):
    PROCESSED = "PROCESSED", "Processed"
    IGNORED = "IGNORED", "Ignored"
    REJECTED = "REJECTED", "Rejected (needs review)"


class WebhookEvent(models.Model):
    """The inbox of provider events: one row per event id, written in the same transaction as
    the event's effects, so an event is applied at most once however often it is delivered."""

    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    payload_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=WebhookEventStatus.choices)
    outcome = models.CharField(max_length=48, blank=True, default="")
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, null=True, blank=True, related_name="webhook_events"
    )
    received_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "webhook_events"
        ordering = ("-received_at", "-id")
        constraints = (
            models.UniqueConstraint(fields=("provider", "event_id"), name="webhook_events_uniq"),
            models.CheckConstraint(
                condition=Q(status__in=WebhookEventStatus.values),
                name="webhook_events_status_valid",
            ),
        )

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id} ({self.status})"
