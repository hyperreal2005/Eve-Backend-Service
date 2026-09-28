from datetime import time

import pytest

from apps.catalog.errors import (
    CentreAlreadyExists,
    DiagnosticTestCodeAlreadyExists,
    DiagnosticTestInactive,
    OfferingAlreadyExists,
)
from apps.catalog.models import DiagnosticCategory
from apps.catalog.services import (
    centre_create,
    centre_deactivate,
    centre_update,
    diagnostic_test_create,
    diagnostic_test_update,
    offering_create,
    offering_deactivate,
    offering_update,
)
from apps.catalog.tests.factories import (
    DiagnosticCentreFactory,
    DiagnosticTestFactory,
    OfferingFactory,
)

pytestmark = pytest.mark.django_db

CENTRE = {
    "name": "Sector 29 Diagnostics",
    "address_line": "Plot 14, Sector 29",
    "city": "Gurugram",
    "state": "Haryana",
    "pincode": "122001",
    "opens_at": time(7, 0),
    "closes_at": time(20, 0),
}


def test_centre_create_defaults_to_indian_time_and_active():
    centre = centre_create(**CENTRE)
    assert (centre.timezone, centre.is_active) == ("Asia/Kolkata", True)


def test_centre_create_rejects_a_duplicate_ignoring_case():
    centre_create(**CENTRE)
    with pytest.raises(CentreAlreadyExists):
        centre_create(**{**CENTRE, "name": CENTRE["name"].upper(), "city": "gurugram"})


def test_centre_update_rejects_a_rename_onto_another_centre():
    DiagnosticCentreFactory(name="Taken", city="Gurugram")
    centre = DiagnosticCentreFactory(name="Mine", city="Gurugram")
    with pytest.raises(CentreAlreadyExists):
        centre_update(centre=centre, changes={"name": "taken"})


def test_centre_deactivate_is_idempotent():
    centre = DiagnosticCentreFactory()
    centre_deactivate(centre=centre)
    centre_deactivate(centre=centre)
    centre.refresh_from_db()
    assert centre.is_active is False


def test_test_codes_are_normalised_and_unique_ignoring_case():
    test = diagnostic_test_create(
        code=" mri_brain ", name="MRI", category=DiagnosticCategory.RADIOLOGY
    )
    assert test.code == "MRI_BRAIN"
    with pytest.raises(DiagnosticTestCodeAlreadyExists):
        diagnostic_test_create(code="Mri_Brain", name="Dup", category=DiagnosticCategory.RADIOLOGY)


def test_test_update_normalises_a_new_code():
    test = DiagnosticTestFactory()
    assert diagnostic_test_update(test=test, changes={"code": "hba1c"}).code == "HBA1C"


def test_offering_create_rejects_a_duplicate():
    offering = OfferingFactory()
    with pytest.raises(OfferingAlreadyExists):
        offering_create(centre=offering.centre, test=offering.test, price=99_900)


def test_an_inactive_test_cannot_be_offered():
    with pytest.raises(DiagnosticTestInactive):
        offering_create(
            centre=DiagnosticCentreFactory(),
            test=DiagnosticTestFactory(is_active=False),
            price=50_000,
        )


def test_offering_price_changes_and_deactivation():
    offering = OfferingFactory(price=100_000)
    offering_update(offering=offering, changes={"price": 120_000})
    offering_deactivate(offering=offering)
    offering.refresh_from_db()
    assert (offering.price, offering.is_active) == (120_000, False)
