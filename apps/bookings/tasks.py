from celery import shared_task

from apps.bookings.services import booking_expire_stale_holds


@shared_task(name="bookings.expire_stale_holds")
def expire_stale_holds() -> int:
    return booking_expire_stale_holds()
