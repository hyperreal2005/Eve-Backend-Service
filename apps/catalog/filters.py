from uuid import UUID

import django_filters
from django.db.models import Exists, OuterRef, Q, QuerySet

from apps.catalog.models import DiagnosticCategory, DiagnosticCentre, DiagnosticTest, Offering


class CentreFilter(django_filters.FilterSet):
    city = django_filters.CharFilter(field_name="city", lookup_expr="iexact")
    search = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Name contains"
    )
    test = django_filters.UUIDFilter(
        method="filter_offers_test", label="Only centres that currently offer this test (id)"
    )

    class Meta:
        model = DiagnosticCentre
        fields = ()

    def filter_offers_test(
        self, queryset: QuerySet[DiagnosticCentre], name: str, value: UUID
    ) -> QuerySet[DiagnosticCentre]:
        # EXISTS rather than a join: one row per centre, however it's offered.
        bookable = Offering.objects.available().filter(centre=OuterRef("pk"), test_id=value)
        return queryset.filter(Exists(bookable))


class DiagnosticTestFilter(django_filters.FilterSet):
    category = django_filters.ChoiceFilter(choices=DiagnosticCategory.choices)
    search = django_filters.CharFilter(method="filter_search", label="Name or code contains")

    class Meta:
        model = DiagnosticTest
        fields = ()

    def filter_search(
        self, queryset: QuerySet[DiagnosticTest], name: str, value: str
    ) -> QuerySet[DiagnosticTest]:
        return queryset.filter(Q(name__icontains=value) | Q(code__icontains=value))


class OfferingFilter(django_filters.FilterSet):
    """Filters for "where is this test offered?"."""

    city = django_filters.CharFilter(field_name="centre__city", lookup_expr="iexact")

    class Meta:
        model = Offering
        fields = ()
