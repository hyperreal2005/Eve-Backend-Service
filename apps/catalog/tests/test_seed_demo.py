from io import StringIO

import pytest
from django.core.management import call_command

from apps.accounts.models import Role, User
from apps.catalog.models import DiagnosticCentre, DiagnosticTest, Offering

pytestmark = pytest.mark.django_db


def seed() -> None:
    call_command("seed_demo", stdout=StringIO())


def counts() -> tuple[int, int, int, int]:
    return (
        DiagnosticCentre.objects.count(),
        DiagnosticTest.objects.count(),
        Offering.objects.count(),
        User.objects.count(),
    )


def test_seed_demo_creates_a_usable_demo_and_is_idempotent():
    seed()
    first = counts()
    seed()

    assert counts() == first
    assert first[:2] == (5, 14)
    assert User.objects.get(email="ops@eve.test").role == Role.ADMIN
    assert User.objects.get(email="patient@eve.test").role == Role.PATIENT


def test_seed_demo_never_overwrites_existing_rows():
    seed()
    Offering.objects.update(price=12_345)
    seed()
    assert set(Offering.objects.values_list("price", flat=True)) == {12_345}


def test_seed_demo_limits_places_only_for_tests_bound_to_one_machine():
    seed()
    mri = Offering.objects.filter(test__code="MRI_BRAIN")
    assert set(mri.values_list("slot_capacity", flat=True)) == {1}
    assert set(
        Offering.objects.filter(test__code="CBC").values_list("slot_capacity", flat=True)
    ) == {None}
