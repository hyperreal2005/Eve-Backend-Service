"""Pure booking rules: the state machine and the appointment-time policy.

No database and no clock: callers pass `now` in, so every rule is testable at its boundaries.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.bookings.models import BookingStatus

# FAILED and CANCELLED are terminal. A failed booking is re-attempted as a new booking, which
# re-checks the price and the appointment rules.
ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    BookingStatus.PENDING: frozenset(
        {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED}
    ),
    BookingStatus.CONFIRMED: frozenset({BookingStatus.CANCELLED}),
    BookingStatus.FAILED: frozenset(),
    BookingStatus.CANCELLED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


@dataclass(frozen=True)
class AppointmentPolicy:
    min_lead: timedelta
    max_advance: timedelta
    slot_minutes: int


@dataclass(frozen=True)
class RuleViolation:
    code: str
    message: str


def check_appointment(
    appointment_at: datetime,
    *,
    now: datetime,
    opens_at: time,
    closes_at: time,
    timezone: str,
    policy: AppointmentPolicy,
) -> RuleViolation | None:
    """The first rule `appointment_at` breaks, or None if it is bookable.

    Opening hours are wall-clock times in the centre's own time zone; the whole slot must fit
    inside them.
    """
    if appointment_at <= now:
        return RuleViolation("in_past", "The appointment must be in the future.")
    if appointment_at < now + policy.min_lead:
        minutes = int(policy.min_lead.total_seconds() // 60)
        return RuleViolation("too_soon", f"Book at least {minutes} minutes ahead.")
    if appointment_at > now + policy.max_advance:
        return RuleViolation("too_far_ahead", f"Book at most {policy.max_advance.days} days ahead.")

    local = appointment_at.astimezone(ZoneInfo(timezone))
    if local.minute % policy.slot_minutes or local.second or local.microsecond:
        return RuleViolation(
            "not_on_slot_boundary",
            f"Appointments start every {policy.slot_minutes} minutes (e.g. 10:00, "
            f"10:{policy.slot_minutes:02d}).",
        )

    start = local.replace(tzinfo=None)
    end = start + timedelta(minutes=policy.slot_minutes)
    if start < datetime.combine(start.date(), opens_at) or end > datetime.combine(
        start.date(), closes_at
    ):
        return RuleViolation(
            "outside_opening_hours",
            f"The centre is open {opens_at:%H:%M}-{closes_at:%H:%M} ({timezone}).",
        )
    return None
