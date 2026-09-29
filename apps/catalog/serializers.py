from functools import cache
from typing import Any
from zoneinfo import available_timezones

from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from apps.catalog.models import (
    DEFAULT_TIMEZONE,
    DiagnosticCategory,
    DiagnosticCentre,
    DiagnosticTest,
    Offering,
)
from apps.core.serializers import StrictInputSerializer

# A generous upper bound (₹10 lakh) that still catches an extra zero typed into a price.
MAX_PRICE_PAISE = 100_000_000
PRICE_HELP = "In paise (1/100 rupee): 150000 means ₹1,500.00."
MAX_SLOT_CAPACITY = 1000
CAPACITY_HELP = "Patients per appointment slot (e.g. 1 for an MRI scanner); null means no limit."


@cache
def _known_timezones() -> frozenset[str]:
    return frozenset(available_timezones())


# ------------------------------------------------------------------------------------ inputs


class CentreWriteSerializer(StrictInputSerializer):
    """Create a centre, or (with partial=True) update one."""

    name = serializers.CharField(max_length=200)
    address_line = serializers.CharField(max_length=300)
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100)
    pincode = serializers.RegexField(
        r"^[1-9][0-9]{5}$", error_messages={"invalid": "Enter a 6-digit PIN code."}
    )
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        required=False,
        allow_null=True,
    )
    timezone = serializers.CharField(max_length=64, default=DEFAULT_TIMEZONE)
    opens_at = serializers.TimeField(help_text="Local time in the centre's time zone.")
    closes_at = serializers.TimeField(help_text="Local time in the centre's time zone.")
    is_active = serializers.BooleanField(required=False)

    def validate_timezone(self, value: str) -> str:
        if value not in _known_timezones():
            raise serializers.ValidationError("Unknown IANA time zone.", code="invalid")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # On a partial update, compare against the stored value of the field not being changed.
        opens = attrs.get("opens_at", getattr(self.instance, "opens_at", None))
        closes = attrs.get("closes_at", getattr(self.instance, "closes_at", None))
        if opens is not None and closes is not None and opens >= closes:
            error = ErrorDetail("Closing time must be after opening time.", code="before_opening")
            raise serializers.ValidationError({"closes_at": [error]})
        return attrs


class DiagnosticTestWriteSerializer(StrictInputSerializer):
    code = serializers.RegexField(
        r"^[A-Za-z0-9_]{2,32}$",
        help_text="Short unique code, stored upper-case (e.g. MRI_BRAIN).",
        error_messages={"invalid": "Use 2-32 letters, digits or underscores."},
    )
    name = serializers.CharField(max_length=200)
    category = serializers.ChoiceField(choices=DiagnosticCategory.choices)
    description = serializers.CharField(required=False, allow_blank=True)
    preparation = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


def _slot_capacity_field() -> serializers.IntegerField:
    return serializers.IntegerField(
        min_value=1,
        max_value=MAX_SLOT_CAPACITY,
        allow_null=True,
        required=False,
        help_text=CAPACITY_HELP,
    )


class OfferingCreateSerializer(StrictInputSerializer):
    test_id = serializers.UUIDField()
    price = serializers.IntegerField(min_value=1, max_value=MAX_PRICE_PAISE, help_text=PRICE_HELP)
    slot_capacity = _slot_capacity_field()
    is_active = serializers.BooleanField(required=False)


class OfferingUpdateSerializer(StrictInputSerializer):
    price = serializers.IntegerField(
        min_value=1, max_value=MAX_PRICE_PAISE, required=False, help_text=PRICE_HELP
    )
    slot_capacity = _slot_capacity_field()
    is_active = serializers.BooleanField(required=False)


# ----------------------------------------------------------------------------------- outputs


class CentreSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticCentre
        fields = (
            "id",
            "name",
            "address_line",
            "city",
            "state",
            "pincode",
            "latitude",
            "longitude",
            "timezone",
            "opens_at",
            "closes_at",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class CentreSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticCentre
        fields = ("id", "name", "address_line", "city", "pincode")
        read_only_fields = fields


class DiagnosticTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = (
            "id",
            "code",
            "name",
            "category",
            "description",
            "preparation",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class DiagnosticTestSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = ("id", "code", "name", "category")
        read_only_fields = fields


class OfferingSerializer(serializers.ModelSerializer):
    """A test as offered at one centre."""

    test = DiagnosticTestSummarySerializer()
    price = serializers.IntegerField(read_only=True, help_text=PRICE_HELP)
    slot_capacity = serializers.IntegerField(
        read_only=True, allow_null=True, help_text=CAPACITY_HELP
    )

    class Meta:
        model = Offering
        fields = ("test", "price", "currency", "slot_capacity", "is_active", "updated_at")
        read_only_fields = fields


class CentreOfferingSerializer(serializers.ModelSerializer):
    """One centre's offer of a test: an entry in a price comparison."""

    centre = CentreSummarySerializer()
    price = serializers.IntegerField(read_only=True, help_text=PRICE_HELP)

    class Meta:
        model = Offering
        fields = ("centre", "price", "currency", "is_active")
        read_only_fields = fields


class CentreDetailSerializer(CentreSerializer):
    tests = OfferingSerializer(source="listed_offerings", many=True)

    class Meta(CentreSerializer.Meta):
        fields = (*CentreSerializer.Meta.fields, "tests")
        read_only_fields = fields
