from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar

from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail


class AwareDateTimeField(serializers.DateTimeField):
    """Rejects datetimes without a UTC offset instead of silently assuming one.

    "10:30" with no offset is ambiguous: read as UTC it's 16:00 in India. Asking the client to be
    explicit is the only way to never book the wrong time.
    """

    default_error_messages: ClassVar[dict[str, str]] = {
        "timezone_required": "Include a UTC offset, e.g. 2026-10-05T10:30:00+05:30.",
    }

    def enforce_timezone(self, value: datetime) -> datetime:
        if timezone.is_naive(value):
            self.fail("timezone_required")
        return super().enforce_timezone(value)


class BlankAsNullChoiceField(serializers.ChoiceField):
    """Renders an empty choice (stored as "") as null."""

    def to_representation(self, value: Any) -> Any:
        return value or None


class StrictInputSerializer(serializers.Serializer):
    """An input serializer that rejects unknown fields instead of silently ignoring them.

    Catches client typos (`apointment_at`) and attempts to set server-owned fields (`amount`,
    `status`, `role`) with a clear 400 rather than letting them pass unnoticed.
    """

    def to_internal_value(self, data: Any) -> Any:
        unknown: list[str] = []
        if isinstance(data, Mapping):
            writable = {name for name, field in self.fields.items() if not field.read_only}
            unknown = sorted(set(data) - writable)
        unknown_errors = {
            name: [ErrorDetail("Unknown field.", code="unknown_field")] for name in unknown
        }
        try:
            value = super().to_internal_value(data)
        except serializers.ValidationError as exc:
            if unknown_errors and isinstance(exc.detail, dict):
                exc.detail.update(unknown_errors)
            raise
        if unknown_errors:
            raise serializers.ValidationError(unknown_errors)
        return value


class FieldErrorSerializer(serializers.Serializer):
    field = serializers.CharField(allow_null=True)
    code = serializers.CharField()
    message = serializers.CharField()


class ProblemDetailSerializer(serializers.Serializer):
    """RFC 9457 problem details, as returned by every error response (documentation only)."""

    type = serializers.CharField()
    title = serializers.CharField()
    status = serializers.IntegerField()
    detail = serializers.CharField()
    instance = serializers.CharField()
    code = serializers.CharField(help_text="Stable, machine-readable error code.")
    request_id = serializers.CharField(help_text="Echoed in the X-Request-ID response header.")
    errors = FieldErrorSerializer(many=True, required=False, help_text="Field errors (400 only).")
