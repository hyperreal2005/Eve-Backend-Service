from uuid import UUID

from django.conf import settings
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bookings.filters import BookingFilter
from apps.bookings.models import Booking
from apps.bookings.selectors import booking_get, booking_list, offering_availability
from apps.bookings.serializers import (
    AvailabilityQuerySerializer,
    AvailabilitySerializer,
    BookingCreateSerializer,
    BookingDetailSerializer,
    BookingSerializer,
)
from apps.bookings.services import booking_cancel, booking_create
from apps.catalog.selectors import centre_get, offering_get
from apps.core.idempotency import idempotency_key
from apps.core.openapi import IDEMPOTENCY_KEY_PARAMETER, problem_responses
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
        parameters=[IDEMPOTENCY_KEY_PARAMETER],
        request=BookingCreateSerializer,
        responses={201: BookingDetailSerializer, **problem_responses(400, 401, 404, 409, 422)},
    )
    def post(self, request: Request) -> Response:
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        booking = booking_create(
            user=request.user, idempotency_key=idempotency_key(request, data), **data
        )
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


class OfferingAvailabilityApi(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        tags=TAGS,
        summary="Free slots for a test at a centre on a day",
        description=(
            "Every slot within the centre's opening hours on `date`, with the places left and "
            "whether a booking for it would be accepted right now."
        ),
        parameters=[AvailabilityQuerySerializer],
        responses={200: AvailabilitySerializer, **problem_responses(400, 404)},
    )
    def get(self, request: Request, centre_id: UUID, test_id: UUID) -> Response:
        query = AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        centre_get(centre_id=centre_id)  # CENTRE_NOT_FOUND rather than OFFERING_NOT_FOUND
        offering = offering_get(centre_id=centre_id, test_id=test_id)
        day = query.validated_data["date"]
        slots = offering_availability(offering=offering, day=day, now=timezone.now())
        data = {
            "date": day,
            "timezone": offering.centre.timezone,
            "slot_minutes": settings.BOOKING_SLOT_MINUTES,
            "slot_capacity": offering.slot_capacity,
            "slots": slots,
        }
        return Response(AvailabilitySerializer(data).data)


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
