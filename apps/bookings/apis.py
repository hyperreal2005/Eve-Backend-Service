from uuid import UUID

from django.db.models import QuerySet
from django.urls import reverse
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bookings.filters import BookingFilter
from apps.bookings.models import Booking
from apps.bookings.selectors import booking_get, booking_list
from apps.bookings.serializers import (
    BookingCreateSerializer,
    BookingDetailSerializer,
    BookingSerializer,
)
from apps.bookings.services import booking_cancel, booking_create
from apps.core.openapi import problem_responses
from apps.core.pagination import NewestFirstCursorPagination

TAGS = ["bookings"]


class BookingListCreateApi(GenericAPIView):
    serializer_class = BookingSerializer
    filterset_class = BookingFilter
    filter_backends = (DjangoFilterBackend,)
    pagination_class = NewestFirstCursorPagination

    def get_queryset(self) -> QuerySet[Booking]:
        if getattr(self, "swagger_fake_view", False):  # schema generation has no real user
            return Booking.objects.none()
        return booking_list(user=self.request.user)

    @extend_schema(
        tags=TAGS,
        summary="My bookings, newest first (administrators: all bookings)",
        responses={200: BookingSerializer(many=True), **problem_responses(400, 401)},
    )
    def get(self, request: Request) -> Response:
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(BookingSerializer(page, many=True).data)

    @extend_schema(
        tags=TAGS,
        summary="Book a test",
        description=(
            "Creates a PENDING booking at the offering's current price. Pay for it with "
            "`POST /api/v1/payments/` before `hold_expires_at`."
        ),
        request=BookingCreateSerializer,
        responses={201: BookingDetailSerializer, **problem_responses(400, 401, 404, 409, 422)},
    )
    def post(self, request: Request) -> Response:
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = booking_create(user=request.user, **serializer.validated_data)
        booking = booking_get(booking_id=booking.id, user=request.user)
        location = reverse("bookings:booking-detail", kwargs={"booking_id": booking.id})
        return Response(
            BookingDetailSerializer(booking).data,
            status=status.HTTP_201_CREATED,
            headers={"Location": request.build_absolute_uri(location)},
        )


class BookingDetailApi(APIView):
    @extend_schema(
        tags=TAGS,
        summary="A booking with its status history",
        responses={200: BookingDetailSerializer, **problem_responses(401, 404)},
    )
    def get(self, request: Request, booking_id: UUID) -> Response:
        booking = booking_get(booking_id=booking_id, user=request.user)
        return Response(BookingDetailSerializer(booking).data)


class BookingCancelApi(APIView):
    @extend_schema(
        tags=TAGS,
        summary="Cancel a booking",
        description=(
            "A pending booking can be cancelled at any time; a confirmed one until the cutoff "
            "before the appointment. Cancelling an already-cancelled booking returns it unchanged."
        ),
        request=None,
        responses={200: BookingDetailSerializer, **problem_responses(401, 404, 409)},
    )
    def post(self, request: Request, booking_id: UUID) -> Response:
        booking_cancel(booking_id=booking_id, user=request.user)
        booking = booking_get(booking_id=booking_id, user=request.user)
        return Response(BookingDetailSerializer(booking).data)
