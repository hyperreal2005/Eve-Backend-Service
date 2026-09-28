import django_filters

from apps.bookings.models import Booking, BookingStatus


class BookingFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=BookingStatus.choices)
    appointment_from = django_filters.IsoDateTimeFilter(
        field_name="appointment_at", lookup_expr="gte", label="Appointments at or after"
    )
    appointment_to = django_filters.IsoDateTimeFilter(
        field_name="appointment_at", lookup_expr="lte", label="Appointments at or before"
    )

    class Meta:
        model = Booking
        fields = ()
