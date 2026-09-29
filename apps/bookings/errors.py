from apps.core.errors import Conflict, DomainError, NotFound


class BookingNotFound(NotFound):
    code = "BOOKING_NOT_FOUND"
    title = "Booking not found"
    # Also what other patients' bookings look like: "not yours" and "doesn't exist" are the same.
    default_detail = "No booking with this id."


class NotOffered(DomainError):
    status_code = 422
    code = "TEST_NOT_OFFERED"
    title = "Test not offered at this centre"
    default_detail = "This centre does not currently offer this test."


class DuplicateBooking(Conflict):
    code = "DUPLICATE_BOOKING"
    title = "Duplicate booking"
    default_detail = "You already have an active booking for this test, centre and time."


class SlotFull(Conflict):
    code = "SLOT_FULL"
    title = "Slot fully booked"
    default_detail = "This time is fully booked for this test at this centre. Choose another slot."


class CancellationWindowClosed(Conflict):
    code = "CANCELLATION_WINDOW_CLOSED"
    title = "Too late to cancel"
    default_detail = (
        "Confirmed bookings can only be cancelled up to the cutoff before the appointment."
    )
