"""The pure booking rules, tested exhaustively and at their exact boundaries."""

from datetime import UTC, datetime, time, timedelta
from itertools import product

import pytest

from apps.bookings.domain import AppointmentPolicy, can_transition, check_appointment
from apps.bookings.models import BookingStatus

PENDING, CONFIRMED, FAILED, CANCELLED = (
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.FAILED,
    BookingStatus.CANCELLED,
)
ALLOWED = {
    (PENDING, CONFIRMED),
    (PENDING, FAILED),
    (PENDING, CANCELLED),
    (CONFIRMED, CANCELLED),
}


@pytest.mark.parametrize(("current", "target"), list(product(BookingStatus, repeat=2)))
def test_the_transition_table(current, target):
    """Every one of the 16 (from, to) pairs; FAILED and CANCELLED are terminal."""
    assert can_transition(current, target) is ((current, target) in ALLOWED)


# ------------------------------------------------------------------------ appointment policy

POLICY = AppointmentPolicy(
    min_lead=timedelta(minutes=60), max_advance=timedelta(days=30), slot_minutes=15
)
# 04:30 UTC is 10:00 in India. The centre is open 07:00-21:00 India time.
NOW = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)
HOURS = {"opens_at": time(7, 0), "closes_at": time(21, 0), "timezone": "Asia/Kolkata"}


def violation(appointment_at: datetime) -> str | None:
    result = check_appointment(appointment_at, now=NOW, policy=POLICY, **HOURS)
    return result.code if result else None


@pytest.mark.parametrize(
    ("appointment_at", "expected"),
    [
        (NOW - timedelta(minutes=15), "in_past"),
        (NOW, "in_past"),
        (NOW + timedelta(minutes=45), "too_soon"),
        (NOW + timedelta(minutes=60), None),  # exactly the minimum lead time
        (NOW + timedelta(days=30), None),  # exactly the maximum advance
        (NOW + timedelta(days=30, minutes=15), "too_far_ahead"),
        (NOW + timedelta(hours=2, minutes=7), "not_on_slot_boundary"),
        (NOW + timedelta(hours=2, seconds=30), "not_on_slot_boundary"),
    ],
)
def test_lead_time_horizon_and_slot_boundaries(appointment_at, expected):
    assert violation(appointment_at) == expected


@pytest.mark.parametrize(
    ("utc_hour", "utc_minute", "expected"),
    [
        (1, 15, "outside_opening_hours"),  # 06:45 India: before opening
        (1, 30, None),  # 07:00 India: the first slot
        (15, 15, None),  # 20:45 India: the last slot, ending exactly at closing
        (15, 30, "outside_opening_hours"),  # 21:00 India: would end after closing
    ],
)
def test_opening_hours_are_checked_in_the_centres_time_zone(utc_hour, utc_minute, expected):
    appointment_at = datetime(2026, 10, 2, utc_hour, utc_minute, tzinfo=UTC)
    assert violation(appointment_at) == expected
