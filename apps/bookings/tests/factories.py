from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.bookings.models import Booking, BookingStatus
from apps.catalog.tests.factories import OfferingFactory

IST = ZoneInfo("Asia/Kolkata")


def future_slot(days: int = 2, hour: int = 10, minute: int = 0) -> datetime:
    """A bookable appointment time: `days` from today at hour:minute, India time."""
    day = timezone.now().astimezone(IST).date() + timedelta(days=days)
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


class BookingFactory(DjangoModelFactory):
    class Meta:
        model = Booking

    user = factory.SubFactory(UserFactory)
    offering = factory.SubFactory(OfferingFactory)
    appointment_at = factory.LazyFunction(future_slot)
    amount = factory.LazyAttribute(lambda booking: booking.offering.price)
    currency = "INR"
    status = BookingStatus.PENDING
    hold_expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(minutes=15))
    confirmed_at = factory.LazyAttribute(
        lambda booking: timezone.now() if booking.status == BookingStatus.CONFIRMED else None
    )
    cancelled_at = factory.LazyAttribute(
        lambda booking: timezone.now() if booking.status == BookingStatus.CANCELLED else None
    )
