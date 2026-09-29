"""Routes of version 1 of the public API, mounted under /api/v1/."""

from django.urls import include, path

from apps.bookings.apis import OfferingAvailabilityApi

urlpatterns = [
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.catalog.urls")),
    # Under the offering's URL, but served by the bookings app: the catalogue knows nothing of
    # bookings.
    path(
        "centres/<uuid:centre_id>/tests/<uuid:test_id>/availability/",
        OfferingAvailabilityApi.as_view(),
        name="offering-availability",
    ),
    path("bookings/", include("apps.bookings.urls")),
    path("payments/", include("apps.payments.urls")),
    path("health/", include("apps.core.urls")),
]
