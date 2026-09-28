"""The database itself rejects invalid catalogue data, whatever path the write takes."""

from datetime import time
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.catalog.models import Offering
from apps.catalog.tests.factories import (
    DiagnosticCentreFactory,
    DiagnosticTestFactory,
    OfferingFactory,
)

pytestmark = pytest.mark.django_db


def assert_rejected(write) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        write()


def test_price_must_be_positive():
    offering = OfferingFactory()
    assert_rejected(lambda: Offering.objects.filter(pk=offering.pk).update(price=0))


def test_a_centre_offers_each_test_at_most_once():
    offering = OfferingFactory()
    assert_rejected(lambda: OfferingFactory(centre=offering.centre, test=offering.test))


def test_centre_name_is_unique_per_city_ignoring_case():
    DiagnosticCentreFactory(name="Sector 29 Diagnostics", city="Gurugram")
    assert_rejected(lambda: DiagnosticCentreFactory(name="SECTOR 29 diagnostics", city="gurugram"))
    # The same name in another city is a different centre.
    assert DiagnosticCentreFactory(name="Sector 29 Diagnostics", city="Noida")


@pytest.mark.parametrize(
    "fields",
    [
        {"pincode": "012345"},
        {"pincode": "12345"},
        {"opens_at": time(21, 0), "closes_at": time(7, 0)},
        {"opens_at": time(9, 0), "closes_at": time(9, 0)},
        {"latitude": Decimal("91")},
        {"longitude": Decimal("-181")},
    ],
)
def test_invalid_centre_fields_are_rejected(fields):
    assert_rejected(lambda: DiagnosticCentreFactory(**fields))


@pytest.mark.parametrize("fields", [{"code": "mri_brain"}, {"code": "X"}, {"category": "DENTAL"}])
def test_invalid_test_fields_are_rejected(fields):
    assert_rejected(lambda: DiagnosticTestFactory(**fields))


def test_referenced_catalogue_rows_cannot_be_deleted():
    offering = OfferingFactory()
    with pytest.raises(ProtectedError):
        offering.test.delete()
    with pytest.raises(ProtectedError):
        offering.centre.delete()


def test_available_offerings_need_an_active_offering_test_and_centre():
    bookable = OfferingFactory()
    OfferingFactory(is_active=False)
    OfferingFactory(test=DiagnosticTestFactory(is_active=False))
    OfferingFactory(centre=DiagnosticCentreFactory(is_active=False))

    assert list(Offering.objects.available()) == [bookable]
