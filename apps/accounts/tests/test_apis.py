import base64
import json
from datetime import timedelta

import jwt
import pytest
import time_machine
from django.conf import settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role, User
from apps.accounts.services import issue_tokens
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory, bearer
from apps.core.tests.assertions import assert_problem, field_errors

SIGNUP_URL = "/api/v1/auth/signup/"
LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/auth/me/"

pytestmark = pytest.mark.django_db


def signup_payload(**overrides):
    return {
        "email": "asha@example.com",
        "password": DEFAULT_PASSWORD,
        "full_name": "Asha Rao",
        **overrides,
    }


# ------------------------------------------------------------------------------------ signup


def test_signup_creates_a_patient_account(api_client):
    response = api_client.post(SIGNUP_URL, signup_payload(email="Asha@Example.com"))

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "asha@example.com"
    assert body["role"] == Role.PATIENT
    assert "password" not in body
    assert User.objects.get(id=body["id"]).check_password(DEFAULT_PASSWORD)


def test_signup_rejects_an_email_registered_in_another_case(api_client):
    UserFactory(email="asha@example.com")
    response = api_client.post(SIGNUP_URL, signup_payload(email="ASHA@example.com"))
    assert_problem(response, 409, "EMAIL_ALREADY_REGISTERED")


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"email": "not-an-email"}, "email", "invalid"),
        ({"password": "short"}, "password", "min_length"),
        ({"password": "x" * 129}, "password", "max_length"),
        ({"password": "password123"}, "password", "password_too_common"),
        ({"password": "83920174655"}, "password", "password_entirely_numeric"),
        ({"full_name": ""}, "full_name", "blank"),
        ({"phone": "98765"}, "phone", "invalid"),
    ],
)
def test_signup_validates_its_input(api_client, overrides, field, code):
    response = api_client.post(SIGNUP_URL, signup_payload(**overrides))
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert (field, code) in field_errors(body)
    assert not User.objects.exists()


def test_signup_reports_every_missing_field(api_client):
    body = assert_problem(api_client.post(SIGNUP_URL, {}), 400, "VALIDATION_ERROR")
    assert {("email", "required"), ("password", "required"), ("full_name", "required")} <= (
        field_errors(body)
    )


def test_signup_cannot_grant_privileges(api_client):
    payload = {**signup_payload(), "role": "ADMIN", "is_superuser": True}
    body = assert_problem(api_client.post(SIGNUP_URL, payload), 400, "VALIDATION_ERROR")
    assert {("role", "unknown_field"), ("is_superuser", "unknown_field")} <= field_errors(body)
    assert not User.objects.exists()


# ------------------------------------------------------------------------------------- login


def test_login_returns_a_token_pair(api_client, user):
    response = api_client.post(
        LOGIN_URL, {"email": user.email.upper(), "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access", "refresh", "token_type", "expires_in"}
    assert (body["token_type"], body["expires_in"]) == ("Bearer", 900)


def test_login_failures_are_indistinguishable(api_client, user):
    wrong_password = api_client.post(LOGIN_URL, {"email": user.email, "password": "Wrong-Pass-1"})
    unknown_email = api_client.post(
        LOGIN_URL, {"email": "nobody@example.com", "password": DEFAULT_PASSWORD}
    )
    bodies = [
        assert_problem(response, 401, "INVALID_CREDENTIALS")
        for response in (wrong_password, unknown_email)
    ]
    for body in bodies:
        del body["request_id"]
    assert bodies[0] == bodies[1]


def test_login_rejects_a_deactivated_account(api_client):
    user = UserFactory(is_active=False)
    response = api_client.post(LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD})
    assert_problem(response, 401, "INVALID_CREDENTIALS")


def test_login_is_rate_limited_per_client(api_client, user):
    credentials = {"email": user.email, "password": "Wrong-Pass-1"}
    for _ in range(10):
        assert api_client.post(LOGIN_URL, credentials).status_code == 401
    response = api_client.post(LOGIN_URL, credentials)
    assert_problem(response, 429, "RATE_LIMITED")
    assert int(response["Retry-After"]) > 0


# ------------------------------------------------------------------------ bearer-token access


def test_me_returns_the_authenticated_user(user_client, user):
    response = user_client.get(ME_URL)
    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)


def test_me_requires_a_token(api_client):
    response = api_client.get(ME_URL)
    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
    assert response["WWW-Authenticate"].startswith("Bearer")


def test_an_expired_access_token_is_rejected(api_client, user):
    header = bearer(user)
    with time_machine.travel(timezone.now() + timedelta(minutes=16), tick=False):
        response = api_client.get(ME_URL, HTTP_AUTHORIZATION=header)
    assert_problem(response, 401, "TOKEN_INVALID")


def test_a_tampered_token_is_rejected(api_client, user):
    head, payload, signature = str(RefreshToken.for_user(user).access_token).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = str(UserFactory().id)
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    response = api_client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {head}.{forged}.{signature}")
    assert_problem(response, 401, "TOKEN_INVALID")


def test_a_refresh_token_is_not_an_access_token(api_client, user):
    refresh = issue_tokens(user).refresh
    response = api_client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {refresh}")
    assert_problem(response, 401, "TOKEN_INVALID")


def _claims(user: User, **overrides) -> dict:
    now = timezone.now()
    return {
        "token_type": "access",
        "sub": str(user.id),
        "jti": "abc123",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "aud": "eve-diagnostics-api",
        "iss": "eve-diagnostics",
        **overrides,
    }


def test_an_unsigned_token_is_rejected(api_client, user):
    token = jwt.encode(_claims(user), key=None, algorithm="none")
    assert_problem(
        api_client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {token}"), 401, "TOKEN_INVALID"
    )


@pytest.mark.parametrize("claim", [{"aud": "another-service"}, {"iss": "someone-else"}])
def test_a_token_minted_for_another_audience_or_issuer_is_rejected(api_client, user, claim):
    key = settings.SIMPLE_JWT["SIGNING_KEY"]
    token = jwt.encode(_claims(user, **claim), key=key, algorithm="HS256")
    assert_problem(
        api_client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {token}"), 401, "TOKEN_INVALID"
    )


def test_a_deactivated_user_loses_access_immediately(user_client, user):
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    assert_problem(user_client.get(ME_URL), 401, "AUTHENTICATION_FAILED")


# --------------------------------------------------------------------------- refresh & logout


def test_refresh_rotates_the_pair_and_revokes_the_old_refresh_token(api_client, user):
    original = issue_tokens(user).refresh

    rotated = api_client.post(REFRESH_URL, {"refresh": original})
    assert rotated.status_code == 200
    assert rotated.json()["refresh"] != original

    reused = api_client.post(REFRESH_URL, {"refresh": original})
    assert_problem(reused, 401, "TOKEN_INVALID")


def test_logout_revokes_the_refresh_token(api_client, user):
    refresh = issue_tokens(user).refresh
    assert api_client.post(LOGOUT_URL, {"refresh": refresh}).status_code == 204
    assert_problem(api_client.post(REFRESH_URL, {"refresh": refresh}), 401, "TOKEN_INVALID")


def test_logout_rejects_an_invalid_token(api_client):
    assert_problem(api_client.post(LOGOUT_URL, {"refresh": "garbage"}), 401, "TOKEN_INVALID")
