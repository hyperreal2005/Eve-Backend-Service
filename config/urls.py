from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.payments.apis import PaymentCreateApi, PaymentWebhookApi

admin.site.site_header = "EVE Diagnostics — operations"
admin.site.site_title = "EVE Diagnostics"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="swagger-ui", permanent=False)),
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.api_v1")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # The assignment brief names these two paths verbatim, so they are served as-is too: the
    # same views, not redirects (a 3xx turns a POST into a GET in most clients, and webhook
    # senders count it as a failed delivery). Only the /api/v1/ routes are documented.
    path("payments/", PaymentCreateApi.as_view(), name="brief-payment-create"),
    path("payments/webhook/", PaymentWebhookApi.as_view(), name="brief-payment-webhook"),
]

# Requests that never reach DRF (unknown URLs, crashes in middleware) still get problem+json.
handler404 = "apps.core.problem_details.page_not_found"
handler500 = "apps.core.problem_details.server_error"
