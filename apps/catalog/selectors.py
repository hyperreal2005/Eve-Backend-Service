"""Read paths for the catalogue. Patients only ever see active records."""

from uuid import UUID

from django.db.models import Prefetch, QuerySet

from apps.catalog.errors import CentreNotFound, DiagnosticTestNotFound, OfferingNotFound
from apps.catalog.models import DiagnosticCentre, DiagnosticTest, Offering


def centre_list(*, include_inactive: bool = False) -> QuerySet[DiagnosticCentre]:
    centres = DiagnosticCentre.objects.all()
    return centres if include_inactive else centres.filter(is_active=True)


def centre_get(*, centre_id: UUID, include_inactive: bool = False) -> DiagnosticCentre:
    try:
        return centre_list(include_inactive=include_inactive).get(id=centre_id)
    except DiagnosticCentre.DoesNotExist:
        raise CentreNotFound() from None


def centre_get_with_offerings(
    *, centre_id: UUID, include_inactive: bool = False
) -> DiagnosticCentre:
    """A centre with its offerings and their tests prefetched into `listed_offerings`."""
    offerings = offering_list(include_inactive=include_inactive).select_related("test")
    centres = centre_list(include_inactive=include_inactive).prefetch_related(
        Prefetch(
            "offerings",
            queryset=offerings.order_by("test__name", "id"),
            to_attr="listed_offerings",
        )
    )
    try:
        return centres.get(id=centre_id)
    except DiagnosticCentre.DoesNotExist:
        raise CentreNotFound() from None


def diagnostic_test_list(*, include_inactive: bool = False) -> QuerySet[DiagnosticTest]:
    tests = DiagnosticTest.objects.all()
    return tests if include_inactive else tests.filter(is_active=True)


def diagnostic_test_get(*, test_id: UUID, include_inactive: bool = False) -> DiagnosticTest:
    try:
        return diagnostic_test_list(include_inactive=include_inactive).get(id=test_id)
    except DiagnosticTest.DoesNotExist:
        raise DiagnosticTestNotFound() from None


def offering_list(*, include_inactive: bool = False) -> QuerySet[Offering]:
    offerings = Offering.objects.all()
    return offerings if include_inactive else offerings.available()


def offering_list_for_centre(
    *, centre_id: UUID, include_inactive: bool = False
) -> QuerySet[Offering]:
    return (
        offering_list(include_inactive=include_inactive)
        .filter(centre_id=centre_id)
        .select_related("test")
    )


def offering_list_for_test(*, test_id: UUID, include_inactive: bool = False) -> QuerySet[Offering]:
    return (
        offering_list(include_inactive=include_inactive)
        .filter(test_id=test_id)
        .select_related("centre")
    )


def offering_get(*, centre_id: UUID, test_id: UUID, include_inactive: bool = False) -> Offering:
    offerings = offering_list(include_inactive=include_inactive).select_related("centre", "test")
    try:
        return offerings.get(centre_id=centre_id, test_id=test_id)
    except Offering.DoesNotExist:
        raise OfferingNotFound() from None
