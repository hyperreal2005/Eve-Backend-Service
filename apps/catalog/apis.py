from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView

from apps.catalog.cache import cached_public_read
from apps.catalog.filters import CentreFilter, DiagnosticTestFilter, OfferingFilter
from apps.catalog.models import DiagnosticCentre, DiagnosticTest, Offering
from apps.catalog.selectors import (
    centre_get,
    centre_get_with_offerings,
    centre_list,
    diagnostic_test_get,
    diagnostic_test_list,
    offering_get,
    offering_list_for_centre,
    offering_list_for_test,
)
from apps.catalog.serializers import (
    CentreDetailSerializer,
    CentreOfferingSerializer,
    CentreSerializer,
    CentreWriteSerializer,
    DiagnosticTestSerializer,
    DiagnosticTestWriteSerializer,
    OfferingCreateSerializer,
    OfferingSerializer,
    OfferingUpdateSerializer,
)
from apps.catalog.services import (
    centre_create,
    centre_deactivate,
    centre_update,
    diagnostic_test_create,
    diagnostic_test_deactivate,
    diagnostic_test_update,
    offering_create,
    offering_deactivate,
    offering_update,
)
from apps.core.openapi import problem_responses
from apps.core.permissions import IsAdminOrReadOnly, is_admin

CENTRES = ["catalog: centres"]
TESTS = ["catalog: tests"]


class _CatalogView(APIView):
    """Anyone may read the catalogue; only administrators may change it."""

    permission_classes = (IsAdminOrReadOnly,)

    @property
    def include_inactive(self) -> bool:
        """Administrators also see inactive records, so they can reactivate them."""
        return is_admin(self.request.user)

    def created(self, data: Any, location: str) -> Response:
        return Response(
            data,
            status=status.HTTP_201_CREATED,
            headers={"Location": self.request.build_absolute_uri(location)},
        )


class _CatalogListView(_CatalogView, GenericAPIView):
    def paginated(self, serializer_class: type[BaseSerializer]) -> Response:
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(serializer_class(page, many=True).data)


# ------------------------------------------------------------------------------------ centres


class CentreListCreateApi(_CatalogListView):
    serializer_class = CentreSerializer
    filterset_class = CentreFilter
    ordering_fields = ("name", "city", "created_at")
    ordering = ("name", "id")

    def get_queryset(self) -> QuerySet[DiagnosticCentre]:
        return centre_list(include_inactive=self.include_inactive)

    @extend_schema(
        tags=CENTRES,
        summary="List diagnostic centres",
        responses={200: CentreSerializer(many=True), **problem_responses(400)},
    )
    @cached_public_read
    def get(self, request: Request) -> Response:
        return self.paginated(CentreSerializer)

    @extend_schema(
        tags=CENTRES,
        summary="Create a centre (administrators)",
        request=CentreWriteSerializer,
        responses={201: CentreSerializer, **problem_responses(400, 401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        serializer = CentreWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        centre = centre_create(**serializer.validated_data)
        location = reverse("catalog:centre-detail", kwargs={"centre_id": centre.id})
        return self.created(CentreSerializer(centre).data, location)


class CentreDetailApi(_CatalogView):
    @extend_schema(
        tags=CENTRES,
        summary="A centre and the tests it offers, with prices",
        responses={200: CentreDetailSerializer, **problem_responses(404)},
    )
    @cached_public_read
    def get(self, request: Request, centre_id: UUID) -> Response:
        centre = centre_get_with_offerings(
            centre_id=centre_id, include_inactive=self.include_inactive
        )
        return Response(CentreDetailSerializer(centre).data)

    @extend_schema(
        tags=CENTRES,
        summary="Update a centre (administrators)",
        request=CentreWriteSerializer(partial=True),
        responses={200: CentreSerializer, **problem_responses(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, centre_id: UUID) -> Response:
        centre = centre_get(centre_id=centre_id, include_inactive=True)
        serializer = CentreWriteSerializer(instance=centre, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        centre = centre_update(centre=centre, changes=serializer.validated_data)
        return Response(CentreSerializer(centre).data)

    @extend_schema(
        tags=CENTRES,
        summary="Deactivate a centre (administrators)",
        description="A soft delete: existing bookings keep their centre. Idempotent.",
        responses={204: None, **problem_responses(401, 403, 404)},
    )
    def delete(self, request: Request, centre_id: UUID) -> Response:
        centre_deactivate(centre=centre_get(centre_id=centre_id, include_inactive=True))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------- offerings of a centre


class CentreOfferingListCreateApi(_CatalogListView):
    serializer_class = OfferingSerializer
    ordering_fields = ("price",)
    ordering = ("test__name", "id")

    def get_queryset(self) -> QuerySet[Offering]:
        if getattr(self, "swagger_fake_view", False):  # schema generation has no URL kwargs
            return Offering.objects.none()
        return offering_list_for_centre(
            centre_id=self.kwargs["centre_id"], include_inactive=self.include_inactive
        )

    @extend_schema(
        tags=CENTRES,
        summary="Tests offered at a centre, with prices",
        responses={200: OfferingSerializer(many=True), **problem_responses(404)},
    )
    @cached_public_read
    def get(self, request: Request, centre_id: UUID) -> Response:
        centre_get(centre_id=centre_id, include_inactive=self.include_inactive)  # 404 if unknown
        return self.paginated(OfferingSerializer)

    @extend_schema(
        tags=CENTRES,
        summary="Offer a test at a centre (administrators)",
        request=OfferingCreateSerializer,
        responses={201: OfferingSerializer, **problem_responses(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, centre_id: UUID) -> Response:
        centre = centre_get(centre_id=centre_id, include_inactive=True)
        serializer = OfferingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        test = diagnostic_test_get(test_id=data.pop("test_id"), include_inactive=True)
        offering = offering_create(centre=centre, test=test, **data)
        location = reverse(
            "catalog:centre-offering-detail", kwargs={"centre_id": centre.id, "test_id": test.id}
        )
        return self.created(OfferingSerializer(offering).data, location)


class CentreOfferingDetailApi(_CatalogView):
    def _offering(self, centre_id: UUID, test_id: UUID, include_inactive: bool) -> Offering:
        centre_get(centre_id=centre_id, include_inactive=include_inactive)  # CENTRE_NOT_FOUND first
        return offering_get(centre_id=centre_id, test_id=test_id, include_inactive=include_inactive)

    @extend_schema(
        tags=CENTRES,
        summary="One test as offered at a centre",
        responses={200: OfferingSerializer, **problem_responses(404)},
    )
    @cached_public_read
    def get(self, request: Request, centre_id: UUID, test_id: UUID) -> Response:
        offering = self._offering(centre_id, test_id, self.include_inactive)
        return Response(OfferingSerializer(offering).data)

    @extend_schema(
        tags=CENTRES,
        summary="Change an offering's price or availability (administrators)",
        description="Existing bookings keep the price they were made at.",
        request=OfferingUpdateSerializer,
        responses={200: OfferingSerializer, **problem_responses(400, 401, 403, 404)},
    )
    def patch(self, request: Request, centre_id: UUID, test_id: UUID) -> Response:
        offering = self._offering(centre_id, test_id, include_inactive=True)
        serializer = OfferingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        offering = offering_update(offering=offering, changes=serializer.validated_data)
        return Response(OfferingSerializer(offering).data)

    @extend_schema(
        tags=CENTRES,
        summary="Stop offering a test at a centre (administrators)",
        description="A soft delete: existing bookings are unaffected. Idempotent.",
        responses={204: None, **problem_responses(401, 403, 404)},
    )
    def delete(self, request: Request, centre_id: UUID, test_id: UUID) -> Response:
        offering_deactivate(offering=self._offering(centre_id, test_id, include_inactive=True))
        return Response(status=status.HTTP_204_NO_CONTENT)


# -------------------------------------------------------------------------------------- tests


class DiagnosticTestListCreateApi(_CatalogListView):
    serializer_class = DiagnosticTestSerializer
    filterset_class = DiagnosticTestFilter
    ordering_fields = ("name", "code", "created_at")
    ordering = ("name", "id")

    def get_queryset(self) -> QuerySet[DiagnosticTest]:
        return diagnostic_test_list(include_inactive=self.include_inactive)

    @extend_schema(
        tags=TESTS,
        summary="List the test catalogue",
        responses={200: DiagnosticTestSerializer(many=True), **problem_responses(400)},
    )
    @cached_public_read
    def get(self, request: Request) -> Response:
        return self.paginated(DiagnosticTestSerializer)

    @extend_schema(
        tags=TESTS,
        summary="Add a test to the catalogue (administrators)",
        request=DiagnosticTestWriteSerializer,
        responses={201: DiagnosticTestSerializer, **problem_responses(400, 401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        serializer = DiagnosticTestWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        test = diagnostic_test_create(**serializer.validated_data)
        location = reverse("catalog:test-detail", kwargs={"test_id": test.id})
        return self.created(DiagnosticTestSerializer(test).data, location)


class DiagnosticTestDetailApi(_CatalogView):
    @extend_schema(
        tags=TESTS,
        summary="A test from the catalogue",
        responses={200: DiagnosticTestSerializer, **problem_responses(404)},
    )
    @cached_public_read
    def get(self, request: Request, test_id: UUID) -> Response:
        test = diagnostic_test_get(test_id=test_id, include_inactive=self.include_inactive)
        return Response(DiagnosticTestSerializer(test).data)

    @extend_schema(
        tags=TESTS,
        summary="Update a test (administrators)",
        request=DiagnosticTestWriteSerializer(partial=True),
        responses={200: DiagnosticTestSerializer, **problem_responses(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, test_id: UUID) -> Response:
        test = diagnostic_test_get(test_id=test_id, include_inactive=True)
        serializer = DiagnosticTestWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        test = diagnostic_test_update(test=test, changes=serializer.validated_data)
        return Response(DiagnosticTestSerializer(test).data)

    @extend_schema(
        tags=TESTS,
        summary="Deactivate a test (administrators)",
        description="A soft delete: it stops being bookable at every centre. Idempotent.",
        responses={204: None, **problem_responses(401, 403, 404)},
    )
    def delete(self, request: Request, test_id: UUID) -> Response:
        diagnostic_test_deactivate(test=diagnostic_test_get(test_id=test_id, include_inactive=True))
        return Response(status=status.HTTP_204_NO_CONTENT)


class DiagnosticTestCentresApi(_CatalogListView):
    """Price comparison: every centre offering a test, cheapest first by default."""

    serializer_class = CentreOfferingSerializer
    filterset_class = OfferingFilter
    ordering_fields = ("price",)
    ordering = ("price", "id")

    def get_queryset(self) -> QuerySet[Offering]:
        if getattr(self, "swagger_fake_view", False):  # schema generation has no URL kwargs
            return Offering.objects.none()
        return offering_list_for_test(
            test_id=self.kwargs["test_id"], include_inactive=self.include_inactive
        )

    @extend_schema(
        tags=TESTS,
        summary="Centres offering a test, with their prices",
        responses={200: CentreOfferingSerializer(many=True), **problem_responses(400, 404)},
    )
    @cached_public_read
    def get(self, request: Request, test_id: UUID) -> Response:
        diagnostic_test_get(test_id=test_id, include_inactive=self.include_inactive)  # 404
        return self.paginated(CentreOfferingSerializer)
