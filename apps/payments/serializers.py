from typing import Any

from rest_framework import serializers

from apps.bookings.models import BookingStatus
from apps.core.serializers import BlankAsNullChoiceField, StrictInputSerializer
from apps.payments.gateway import get_gateway
from apps.payments.models import Payment, PaymentStatus, RefundStatus


class BlankAsNullCharField(serializers.CharField):
    def to_representation(self, value: Any) -> Any:
        return value or None


class PaymentCreateSerializer(StrictInputSerializer):
    booking_id = serializers.UUIDField()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # The methods come from the configured provider, so the schema shows its test methods.
        gateway = get_gateway()
        self.fields["payment_method"] = serializers.ChoiceField(
            choices=list(gateway.payment_methods),
            required=False,
            help_text=(
                f"A test payment method of the configured provider ({gateway.name}). "
                f"Defaults to {gateway.default_method}."
            ),
        )


class PaymentSerializer(serializers.ModelSerializer):
    booking_id = serializers.UUIDField(read_only=True)
    booking_status = serializers.ChoiceField(
        source="booking.status", choices=BookingStatus.choices, read_only=True
    )
    status = serializers.ChoiceField(choices=PaymentStatus.choices, read_only=True)
    amount = serializers.IntegerField(read_only=True, help_text="In paise.")
    provider_payment_id = BlankAsNullCharField(read_only=True, allow_null=True)
    failure_code = BlankAsNullCharField(read_only=True, allow_null=True)
    failure_message = BlankAsNullCharField(read_only=True, allow_null=True)
    refund_status = BlankAsNullChoiceField(
        choices=RefundStatus.choices,
        allow_null=True,
        read_only=True,
        help_text="PENDING means a refund is owed (e.g. paid after the booking was cancelled).",
    )

    class Meta:
        model = Payment
        fields = (
            "id",
            "booking_id",
            "booking_status",
            "status",
            "amount",
            "currency",
            "method",
            "provider",
            "provider_payment_id",
            "failure_code",
            "failure_message",
            "refund_status",
            "created_at",
            "completed_at",
        )
        read_only_fields = fields


class PaymentSummarySerializer(serializers.ModelSerializer):
    """A payment attempt as listed on its booking."""

    status = serializers.ChoiceField(choices=PaymentStatus.choices, read_only=True)
    failure_code = BlankAsNullCharField(read_only=True, allow_null=True)
    refund_status = BlankAsNullChoiceField(
        choices=RefundStatus.choices, allow_null=True, read_only=True
    )

    class Meta:
        model = Payment
        fields = (
            "id",
            "status",
            "amount",
            "method",
            "failure_code",
            "refund_status",
            "created_at",
            "completed_at",
        )
        read_only_fields = fields


class WebhookReceiptSerializer(serializers.Serializer):
    event_id = serializers.CharField()
    status = serializers.ChoiceField(choices=["processed", "duplicate", "ignored", "rejected"])
    outcome = serializers.CharField(
        help_text="applied, noop, refund_owed, stale_event, unsupported_event_type, "
        "unknown_payment, amount_mismatch or provider_payment_id_mismatch."
    )


class _PaymentEventDataSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField(help_text="Our payment id (the charge reference).")
    provider_payment_id = serializers.CharField()
    amount = serializers.IntegerField(help_text="In paise; must equal the payment's amount.")
    currency = serializers.CharField()
    failure_code = serializers.CharField(required=False, allow_null=True)


class ProviderEventSerializer(serializers.Serializer):
    """A provider event, for documentation (the body is verified as raw bytes)."""

    type = serializers.ChoiceField(choices=["payment.succeeded", "payment.failed"])
    timestamp = serializers.DateTimeField()
    data = _PaymentEventDataSerializer()
