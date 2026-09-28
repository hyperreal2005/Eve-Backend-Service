import pytest
from rest_framework import serializers

from apps.core.serializers import StrictInputSerializer


class _Example(StrictInputSerializer):
    name = serializers.CharField()
    created_at = serializers.DateTimeField(read_only=True)


def _errors(data) -> dict:
    serializer = _Example(data=data)
    assert not serializer.is_valid()
    return {field: [error.code for error in errors] for field, errors in serializer.errors.items()}


def test_valid_input_passes():
    serializer = _Example(data={"name": "CBC"})
    assert serializer.is_valid()
    assert serializer.validated_data == {"name": "CBC"}


@pytest.mark.parametrize("unknown", ["amount", "status", "created_at"])
def test_unknown_and_read_only_fields_are_rejected(unknown):
    assert _errors({"name": "CBC", unknown: "x"}) == {unknown: ["unknown_field"]}


def test_unknown_fields_are_reported_together_with_other_errors():
    assert _errors({"amount": 1}) == {"name": ["required"], "amount": ["unknown_field"]}
