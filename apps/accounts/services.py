"""Account workflows: registration, credential login and logout."""

from dataclasses import dataclass

import structlog
from django.contrib.auth import authenticate
from django.contrib.auth.models import update_last_login
from django.http import HttpRequest
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.errors import EmailAlreadyRegistered, InvalidCredentials, InvalidRefreshToken
from apps.accounts.models import Role, User, normalize_email
from apps.core.db import translate_integrity_errors

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TokenPair:
    access: str
    refresh: str
    expires_in: int
    token_type: str = "Bearer"  # noqa: S105 (the OAuth token *type*, not a secret)


def user_create(
    *, email: str, password: str, full_name: str, phone: str = "", role: Role = Role.PATIENT
) -> User:
    """Register a user. The unique constraint decides duplicates, including concurrent ones."""
    with translate_integrity_errors({"users_email_uniq": EmailAlreadyRegistered}):
        user = User.objects.create_user(
            email=normalize_email(email),
            password=password,
            full_name=full_name.strip(),
            phone=phone,
            role=role,
        )
    log.info("user.registered", user_id=str(user.id), role=user.role)
    return user


def auth_login(*, email: str, password: str, request: HttpRequest | None = None) -> TokenPair:
    """Exchange credentials for a token pair.

    An unknown email, a wrong password and an inactive account all raise the same error, and
    Django's backend hashes the password even for unknown emails, so neither the response nor
    its timing reveals which one it was.
    """
    user = authenticate(request, email=normalize_email(email), password=password)
    if user is None:
        log.info("auth.login_failed", email=email)
        raise InvalidCredentials()
    if jwt_settings.UPDATE_LAST_LOGIN:
        update_last_login(None, user)
    log.info("auth.login_succeeded", user_id=str(user.pk))
    return issue_tokens(user)


def auth_logout(*, refresh: str) -> None:
    """Revoke a refresh token. Access tokens are short-lived and expire on their own."""
    try:
        RefreshToken(refresh).blacklist()  # type: ignore[arg-type]
    except TokenError as exc:
        raise InvalidRefreshToken() from exc


def issue_tokens(user: User) -> TokenPair:
    refresh = RefreshToken.for_user(user)
    return TokenPair(
        access=str(refresh.access_token),
        refresh=str(refresh),
        expires_in=int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds()),
    )
