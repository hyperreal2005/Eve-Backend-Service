from rest_framework import serializers

from apps.bookings.models import (
    ActorType,
    Booking,
    BookingStatus,
    BookingStatusEvent,
    BookingStatusReason,
)
from apps.catalog.serializers import CentreSummarySerializer, DiagnosticTestSummarySerializer
from apps.core.serializers import AwareDateTimeField, BlankAsNullChoiceField, StrictInputSerializer


class BookingCreateSerializer(StrictInputSerializer):
    centre_id = serializers.UUIDField()
    test_id = serializers.UUIDField()
    appointment_at = AwareDateTimeField(
        help_text="ISO 8601 with a UTC offset, on a slot boundary within the centre's hours."
    )


class BookingSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(read_only=True)
    status = serializers.ChoiceField(choices=BookingStatus.choices, read_only=True)
    status_reason = BlankAsNullChoiceField(
        choices=BookingStatusReason.choices, allow_null=True, read_only=True
    )
    centre = CentreSummarySerializer(source="offering.centre", read_only=True)
    test = DiagnosticTestSummarySerializer(source="offering.test", read_only=True)
    amount = serializers.IntegerField(
        read_only=True, help_text="In paise; the price when the booking was made."
    )
    hold_expires_at = serializers.DateTimeField(
        read_only=True, help_text="An unpaid booking is released after this."
    )

    class Meta:
        model = Booking
        fields = (
            "id",
            "user_id",
            "status",
            "status_reason",
            "centre",
            "test",
            "appointment_at",
            "amount",
            "currency",
            "hold_expires_at",
            "confirmed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class BookingStatusEventSerializer(serializers.ModelSerializer):
    from_status = BlankAsNullChoiceField(
        choices=BookingStatus.choices, allow_null=True, read_only=True
    )
    to_status = serializers.ChoiceField(choices=BookingStatus.choices, read_only=True)
    reason = BlankAsNullChoiceField(
        choices=BookingStatusReason.choices, allow_null=True, read_only=True
    )
    actor_type = serializers.ChoiceField(choices=ActorType.choices, read_only=True)

    class Meta:
        model = BookingStatusEvent
        fields = ("from_status", "to_status", "reason", "actor_type", "created_at")
        read_only_fields = fields


class BookingDetailSerializer(BookingSerializer):
    history = BookingStatusEventSerializer(source="status_events", many=True, read_only=True)

    class Meta(BookingSerializer.Meta):
        fields = (*BookingSerializer.Meta.fields, "history")
        read_only_fields = fields
