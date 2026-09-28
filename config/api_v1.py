"""Routes of version 1 of the public API, mounted under /api/v1/."""

from django.urls import include, path

urlpatterns = [
    path("auth/", include("apps.accounts.urls")),
    path("health/", include("apps.core.urls")),
]
