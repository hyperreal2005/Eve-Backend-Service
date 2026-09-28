from typing import Any

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from apps.accounts.models import User, normalize_email
from apps.core.serializers import StrictInputSerializer

# Passwords are never trimmed or transformed; the upper bound caps the cost of hashing them.
_PASSWORD = {"write_only": True, "trim_whitespace": False, "style": {"input_type": "password"}}


class SignupSerializer(StrictInputSerializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(min_length=8, max_length=128, **_PASSWORD)
    full_name = serializers.CharField(max_length=120)
    phone = serializers.RegexField(
        r"^\+[1-9]\d{7,14}$",
        max_length=16,
        required=False,
        allow_blank=True,
        error_messages={"invalid": "Use international format, for example +919876543210."},
    )

    def validate_email(self, value: str) -> str:
        return normalize_email(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        candidate = User(email=attrs["email"], full_name=attrs["full_name"])
        try:
            password_validation.validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            errors = [
                ErrorDetail(message, code=error.code or "invalid")
                for error, message in zip(exc.error_list, exc.messages, strict=True)
            ]
            raise serializers.ValidationError({"password": errors}) from exc
        return attrs


class LoginSerializer(StrictInputSerializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, **_PASSWORD)


class RefreshTokenSerializer(StrictInputSerializer):
    refresh = serializers.CharField()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "full_name", "phone", "role", "created_at")
        read_only_fields = fields


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    token_type = serializers.CharField(help_text='Always "Bearer".')
    expires_in = serializers.IntegerField(help_text="Access-token lifetime in seconds.")
