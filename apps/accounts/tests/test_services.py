import pytest
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.errors import EmailAlreadyRegistered, InvalidCredentials, InvalidRefreshToken
from apps.accounts.services import auth_login, auth_logout, issue_tokens, user_create
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db


def test_user_create_normalises_the_email_and_hashes_the_password():
    user = user_create(email="Asha@Example.com", password=DEFAULT_PASSWORD, full_name=" Asha ")
    assert user.email == "asha@example.com"
    assert user.full_name == "Asha"
    assert user.check_password(DEFAULT_PASSWORD)


def test_the_unique_constraint_rejects_an_email_that_differs_only_in_case():
    UserFactory(email="asha@example.com")
    with pytest.raises(EmailAlreadyRegistered):
        user_create(email="ASHA@example.com", password=DEFAULT_PASSWORD, full_name="Asha")


def test_a_rejected_duplicate_leaves_the_enclosing_transaction_usable():
    UserFactory(email="asha@example.com")
    with pytest.raises(EmailAlreadyRegistered):
        user_create(email="asha@example.com", password=DEFAULT_PASSWORD, full_name="Asha")
    # The violation was confined to a savepoint, so this transaction can still be used.
    assert user_create(email="ravi@example.com", password=DEFAULT_PASSWORD, full_name="Ravi")


def test_auth_login_issues_tokens_with_the_expected_claims():
    user = UserFactory()
    tokens = auth_login(email=user.email.upper(), password=DEFAULT_PASSWORD)

    access = AccessToken(tokens.access)  # verifies signature, expiry, audience and issuer
    assert access["sub"] == str(user.id)
    assert access["aud"] == "eve-diagnostics-api"
    assert access["iss"] == "eve-diagnostics"
    assert access["token_type"] == "access"
    assert (tokens.token_type, tokens.expires_in) == ("Bearer", 15 * 60)
    user.refresh_from_db()
    assert user.last_login is not None


@pytest.mark.parametrize("scenario", ["unknown_email", "wrong_password", "inactive_account"])
def test_auth_login_fails_the_same_way_whatever_the_reason(scenario):
    user = UserFactory(is_active=scenario != "inactive_account")
    email = "nobody@example.com" if scenario == "unknown_email" else user.email
    password = "Wrong-Password-1" if scenario == "wrong_password" else DEFAULT_PASSWORD
    with pytest.raises(InvalidCredentials):
        auth_login(email=email, password=password)


def test_auth_logout_revokes_the_refresh_token():
    tokens = issue_tokens(UserFactory())
    auth_logout(refresh=tokens.refresh)
    with pytest.raises(TokenError):
        RefreshToken(tokens.refresh)  # type: ignore[arg-type]


def test_auth_logout_rejects_a_token_it_cannot_verify():
    with pytest.raises(InvalidRefreshToken):
        auth_logout(refresh="not-a-token")
