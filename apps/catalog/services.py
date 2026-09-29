"""Write paths for the catalogue. Only administrators reach them; the API layer enforces that.

Records are deactivated, never deleted: bookings keep referring to the centre, test and offering
they were made against.
"""

from collections.abc import Mapping
from datetime import time
from decimal import Decimal
from typing import Any

import structlog
from django.db.models import Model

from apps.catalog.errors import (
    CentreAlreadyExists,
    DiagnosticTestCodeAlreadyExists,
    DiagnosticTestInactive,
    OfferingAlreadyExists,
)
from apps.catalog.models import (
    DEFAULT_TIMEZONE,
    DiagnosticCategory,
    DiagnosticCentre,
    DiagnosticTest,
    Offering,
)
from apps.core.db import translate_integrity_errors

log = structlog.get_logger(__name__)

_CENTRE_CONFLICTS = {"diagnostic_centres_name_city_uniq": CentreAlreadyExists}
_TEST_CONFLICTS = {"diagnostic_tests_code_uniq": DiagnosticTestCodeAlreadyExists}
_OFFERING_CONFLICTS = {"offerings_centre_test_uniq": OfferingAlreadyExists}


def normalize_test_code(code: str) -> str:
    return code.strip().upper()


# ------------------------------------------------------------------------------------ centres


def centre_create(
    *,
    name: str,
    address_line: str,
    city: str,
    state: str,
    pincode: str,
    opens_at: time,
    closes_at: time,
    timezone: str = DEFAULT_TIMEZONE,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
    is_active: bool = True,
) -> DiagnosticCentre:
    centre = DiagnosticCentre(
        name=name,
        address_line=address_line,
        city=city,
        state=state,
        pincode=pincode,
        opens_at=opens_at,
        closes_at=closes_at,
        timezone=timezone,
        latitude=latitude,
        longitude=longitude,
        is_active=is_active,
    )
    with translate_integrity_errors(_CENTRE_CONFLICTS):
        centre.save()
    log.info("catalog.centre_created", centre_id=str(centre.id))
    return centre


def centre_update(*, centre: DiagnosticCentre, changes: Mapping[str, Any]) -> DiagnosticCentre:
    _apply(centre, changes, _CENTRE_CONFLICTS)
    log.info("catalog.centre_updated", centre_id=str(centre.id), fields=sorted(changes))
    return centre


def centre_deactivate(*, centre: DiagnosticCentre) -> None:
    """Idempotent soft delete: the centre stops being listed and bookable."""
    if centre.is_active:
        _apply(centre, {"is_active": False}, {})
        log.info("catalog.centre_deactivated", centre_id=str(centre.id))


# ------------------------------------------------------------------------------------- tests


def diagnostic_test_create(
    *,
    code: str,
    name: str,
    category: DiagnosticCategory,
    description: str = "",
    preparation: str = "",
    is_active: bool = True,
) -> DiagnosticTest:
    test = DiagnosticTest(
        code=normalize_test_code(code),
        name=name,
        category=category,
        description=description,
        preparation=preparation,
        is_active=is_active,
    )
    with translate_integrity_errors(_TEST_CONFLICTS):
        test.save()
    log.info("catalog.test_created", test_id=str(test.id), code=test.code)
    return test


def diagnostic_test_update(*, test: DiagnosticTest, changes: Mapping[str, Any]) -> DiagnosticTest:
    if "code" in changes:
        changes = {**changes, "code": normalize_test_code(changes["code"])}
    _apply(test, changes, _TEST_CONFLICTS)
    log.info("catalog.test_updated", test_id=str(test.id), fields=sorted(changes))
    return test


def diagnostic_test_deactivate(*, test: DiagnosticTest) -> None:
    """Idempotent soft delete: every offering of the test stops being bookable too."""
    if test.is_active:
        _apply(test, {"is_active": False}, {})
        log.info("catalog.test_deactivated", test_id=str(test.id))


# --------------------------------------------------------------------------------- offerings


def offering_create(
    *,
    centre: DiagnosticCentre,
    test: DiagnosticTest,
    price: int,
    slot_capacity: int | None = None,
    is_active: bool = True,
) -> Offering:
    if not test.is_active:
        raise DiagnosticTestInactive()
    offering = Offering(
        centre=centre, test=test, price=price, slot_capacity=slot_capacity, is_active=is_active
    )
    with translate_integrity_errors(_OFFERING_CONFLICTS):
        offering.save()
    log.info("catalog.offering_created", offering_id=str(offering.id), price=price)
    return offering


def offering_update(*, offering: Offering, changes: Mapping[str, Any]) -> Offering:
    """Price changes apply to new bookings only: bookings keep the price they were made at."""
    _apply(offering, changes, _OFFERING_CONFLICTS)
    log.info("catalog.offering_updated", offering_id=str(offering.id), fields=sorted(changes))
    return offering


def offering_deactivate(*, offering: Offering) -> None:
    if offering.is_active:
        _apply(offering, {"is_active": False}, {})
        log.info("catalog.offering_deactivated", offering_id=str(offering.id))


def _apply(instance: Model, changes: Mapping[str, Any], conflicts: Mapping[str, Any]) -> None:
    for field, value in changes.items():
        setattr(instance, field, value)
    with translate_integrity_errors(conflicts):
        instance.save(update_fields=[*changes, "updated_at"])
