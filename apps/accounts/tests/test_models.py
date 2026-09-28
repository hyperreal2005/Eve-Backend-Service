import pytest
from django.contrib.auth.hashers import Argon2PasswordHasher
from django.db import IntegrityError, transaction

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db


def test_the_manager_normalises_email_and_hashes_the_password():
    user = User.objects.create_user(
        "  Asha.Rao@Example.COM ", password=DEFAULT_PASSWORD, full_name="Asha Rao"
    )
    assert user.email == "asha.rao@example.com"
    assert user.password != DEFAULT_PASSWORD
    assert user.check_password(DEFAULT_PASSWORD)
    assert user.role == Role.PATIENT


def test_the_database_rejects_a_non_canonical_email():
    # bulk_create bypasses the manager, like a raw insert would.
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email="Mixed@Example.com", full_name="Mixed Case")])


def test_the_database_rejects_a_duplicate_email():
    UserFactory(email="taken@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email="taken@example.com", full_name="Second")])


def test_the_database_rejects_an_unknown_role():
    user = UserFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(role="ROOT")


def test_admin_access_follows_the_role():
    patient, admin = UserFactory(), UserFactory(role=Role.ADMIN)
    assert (patient.is_admin, patient.is_staff) == (False, False)
    assert (admin.is_admin, admin.is_staff) == (True, True)


def test_create_superuser_makes_an_administrator():
    user = User.objects.create_superuser("ops@eve.test", DEFAULT_PASSWORD, full_name="EVE Ops")
    assert user.role == Role.ADMIN
    assert user.is_superuser


def test_production_hashes_passwords_with_argon2id():
    from config import settings as production_settings

    assert production_settings.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")
    hasher = Argon2PasswordHasher()
    assert hasher.encode(DEFAULT_PASSWORD, hasher.salt()).startswith("argon2$argon2id$")
