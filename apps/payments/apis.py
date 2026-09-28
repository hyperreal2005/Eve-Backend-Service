from dataclasses import asdict
from uuid import UUID

from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import UnsupportedMediaType
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from apps.core.openapi import problem_responses
from apps.payments.models import PaymentStatus
from apps.payments.selectors import payment_get
from apps.payments.serializers import (
    PaymentCreateSerializer,
    PaymentSerializer,
    ProviderEventSerializer,
    WebhookReceiptSerializer,
)
from apps.payments.services import payment_initiate
from apps.payments.webhooks import webhook_receive

TAGS = ["payments"]


class PaymentCreateApi(APIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = "payments"

    @extend_schema(
        tags=TAGS,
        summary="Pay for a booking (simulated provider)",
        description=(
            "Charges the booking's amount through the configured provider. `201` when the "
            "outcome is known (`SUCCESS` confirms the booking, `FAILED` fails it); `202` when it "
            "is still pending, in which case the result arrives by webhook.\n\n"
            "Also served at `POST /payments/`, the path named in the assignment brief."
        ),
        request=PaymentCreateSerializer,
        responses={
            201: PaymentSerializer,
            202: OpenApiResponse(PaymentSerializer, description="Outcome pending"),
            **problem_responses(400, 401, 404, 409, 429),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = payment_initiate(
            user=request.user,
            booking_id=serializer.validated_data["booking_id"],
            method=serializer.validated_data.get("payment_method"),
        )
        payment = payment_get(payment_id=payment.id, user=request.user)
        location = reverse("payments:payment-detail", kwargs={"payment_id": payment.id})
        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_202_ACCEPTED
            if payment.status == PaymentStatus.PENDING
            else status.HTTP_201_CREATED,
            headers={"Location": request.build_absolute_uri(location)},
        )


class PaymentDetailApi(APIView):
    @extend_schema(
        tags=TAGS,
        summary="A payment's status",
        responses={200: PaymentSerializer, **problem_responses(401, 404)},
    )
    def get(self, request: Request, payment_id: UUID) -> Response:
        return Response(
            PaymentSerializer(payment_get(payment_id=payment_id, user=request.user)).data
        )


class PaymentWebhookApi(APIView):
    """Authenticated by the provider's signature, not by a user token."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = ()

    @extend_schema(
        tags=TAGS,
        auth=[],
        summary="Payment provider webhook (Standard Webhooks signatures)",
        description=(
            "Idempotent: an event id is applied at most once, however often it is delivered, "
            "and results converge whatever order they arrive in. Authentic events are always "
            "acknowledged with 200 (the body says what happened); only unsigned or malformed "
            "requests are refused. Generate a correctly signed request with "
            "`python manage.py send_webhook <payment_id>`.\n\n"
            "Also served at `POST /payments/webhook/`, the path named in the assignment brief."
        ),
        parameters=[
            OpenApiParameter("webhook-id", OpenApiTypes.STR, OpenApiParameter.HEADER, True),
            OpenApiParameter("webhook-timestamp", OpenApiTypes.INT, OpenApiParameter.HEADER, True),
            OpenApiParameter("webhook-signature", OpenApiTypes.STR, OpenApiParameter.HEADER, True),
        ],
        request=ProviderEventSerializer,
        responses={200: WebhookReceiptSerializer, **problem_responses(400, 401, 415, 503)},
    )
    def post(self, request: Request) -> Response:
        if request.content_type.split(";")[0].strip() != "application/json":
            raise UnsupportedMediaType(request.content_type)
        # The raw bytes, exactly as signed: parsed-and-reserialised JSON would not verify.
        receipt = webhook_receive(headers=request.headers, body=request.body)
        return Response(asdict(receipt))
