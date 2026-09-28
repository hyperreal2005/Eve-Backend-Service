from django.db import models
from django.utils import timezone


class ChargeStatus(models.TextChoices):
    """The provider's own vocabulary; the gateway adapter maps it onto ours."""

    PROCESSING = "PROCESSING", "Processing"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"


class MockCharge(models.Model):
    """The provider's ledger (think "Razorpay's database", not ours)."""

    # The merchant's payment id. Charging the same reference again returns this charge.
    reference = models.UUIDField(unique=True)
    provider_payment_id = models.CharField(max_length=64, unique=True)
    amount = models.IntegerField()
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=ChargeStatus.choices)
    failure_code = models.CharField(max_length=64, blank=True, default="")
    # For asynchronous methods: the result the provider will settle on later.
    pending_outcome = models.CharField(
        max_length=16, choices=ChargeStatus.choices, blank=True, default=""
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mockpay_charges"

    def __str__(self) -> str:
        return f"{self.provider_payment_id} ({self.status})"
