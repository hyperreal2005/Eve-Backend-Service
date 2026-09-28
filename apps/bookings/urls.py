from django.urls import path

from apps.bookings.apis import BookingCancelApi, BookingDetailApi, BookingListCreateApi

app_name = "bookings"

urlpatterns = [
    path("", BookingListCreateApi.as_view(), name="booking-list"),
    path("<uuid:booking_id>/", BookingDetailApi.as_view(), name="booking-detail"),
    path("<uuid:booking_id>/cancel/", BookingCancelApi.as_view(), name="booking-cancel"),
]
