from django.urls import path

from apps.payments.apis import PaymentCreateApi, PaymentDetailApi, PaymentWebhookApi

app_name = "payments"

urlpatterns = [
    path("", PaymentCreateApi.as_view(), name="payment-create"),
    path("webhook/", PaymentWebhookApi.as_view(), name="payment-webhook"),
    path("<uuid:payment_id>/", PaymentDetailApi.as_view(), name="payment-detail"),
]
