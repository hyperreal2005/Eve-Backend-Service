from django.urls import path

from apps.catalog.apis import (
    CentreDetailApi,
    CentreListCreateApi,
    CentreOfferingDetailApi,
    CentreOfferingListCreateApi,
    DiagnosticTestCentresApi,
    DiagnosticTestDetailApi,
    DiagnosticTestListCreateApi,
)

app_name = "catalog"

urlpatterns = [
    path("centres/", CentreListCreateApi.as_view(), name="centre-list"),
    path("centres/<uuid:centre_id>/", CentreDetailApi.as_view(), name="centre-detail"),
    path(
        "centres/<uuid:centre_id>/tests/",
        CentreOfferingListCreateApi.as_view(),
        name="centre-offering-list",
    ),
    path(
        "centres/<uuid:centre_id>/tests/<uuid:test_id>/",
        CentreOfferingDetailApi.as_view(),
        name="centre-offering-detail",
    ),
    path("tests/", DiagnosticTestListCreateApi.as_view(), name="test-list"),
    path("tests/<uuid:test_id>/", DiagnosticTestDetailApi.as_view(), name="test-detail"),
    path("tests/<uuid:test_id>/centres/", DiagnosticTestCentresApi.as_view(), name="test-centres"),
]
