from django.urls import path

from apps.core.views import LivenessApi, ReadinessApi

app_name = "core"

urlpatterns = [
    path("live/", LivenessApi.as_view(), name="health-live"),
    path("ready/", ReadinessApi.as_view(), name="health-ready"),
]
