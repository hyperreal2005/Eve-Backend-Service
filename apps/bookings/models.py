from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import Offering
from apps.core.models import BaseModel


class BookingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending payment"
    CONFIRMED = "CONFIRMED", "Confirmed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class BookingStatusReason(models.TextChoices):
    PAYMENT_DECLINED = "PAYMENT_DECLINED", "Payment declined"
    PAYMENT_TIMEOUT = "PAYMENT_TIMEOUT", "Not paid before the hold expired"
    USER_CANCELLED = "USER_CANCELLED", "Cancelled by the patient"
    ADMIN_CANCELLED = "ADMIN_CANCELLED", "Cancelled by EVE operations"


class ActorType(models.TextChoices):
    USER = "USER", "Patient"
    ADMIN = "ADMIN", "Administrator"
    SYSTEM = "SYSTEM", "System"
    PROVIDER = "PROVIDER", "Payment provider"


ACTIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


class BookingQuerySet(models.QuerySet["Booking"]):
    def active(self) -> "BookingQuerySet":
        """Bookings that still hold (or will take) the appointment."""
        return self.filter(status__in=ACTIVE_STATUSES)


class Booking(BaseModel):
    """A patient's reservation of a test at a centre, at a time, for a fixed amount."""

    # user and offering are the leading columns of composite indexes below, so their
    # single-column foreign-key indexes would be redundant.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings", db_index=False
    )
    offering = models.ForeignKey(
        Offering, on_delete=models.PROTECT, related_name="bookings", db_index=False
    )
    appointment_at = models.DateTimeField()
    # A snapshot of the offering's price when the booking was made; later price changes never
    # touch it, and it is what the patient is charged.
    amount = models.IntegerField(help_text="In paise.")
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=16, choices=BookingStatus.choices, default=BookingStatus.PENDING
    )
    status_reason = models.CharField(
        max_length=24, choices=BookingStatusReason.choices, blank=True, default=""
    )
    # Unpaid bookings are released after this (lazily when paying, and by a sweeper).
    hold_expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[BookingQuerySet] = BookingQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        db_table = "bookings"
        constraints = (
            # A double-click can't create two bookings; re-booking after a cancellation can.
            models.UniqueConstraint(
                fields=("user", "offering", "appointment_at"),
                condition=Q(status__in=ACTIVE_STATUSES),
                name="bookings_one_active_per_slot",
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="bookings_amount_positive"),
            models.CheckConstraint(
                condition=Q(status__in=BookingStatus.values), name="bookings_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(status_reason__in=[*BookingStatusReason.values, ""]),
                name="bookings_status_reason_valid",
            ),
            models.CheckConstraint(
                condition=~Q(status=BookingStatus.CONFIRMED) | Q(confirmed_at__isnull=False),
                name="bookings_confirmed_has_timestamp",
            ),
            models.CheckConstraint(
                condition=~Q(status=BookingStatus.CANCELLED) | Q(cancelled_at__isnull=False),
                name="bookings_cancelled_has_timestamp",
            ),
        )
        indexes = (
            models.Index(fields=("user", "-created_at"), name="bookings_user_recent_idx"),
            models.Index(fields=("offering", "appointment_at"), name="bookings_offering_slot_idx"),
            models.Index(
                fields=("hold_expires_at",),
                condition=Q(status=BookingStatus.PENDING),
                name="bookings_pending_hold_idx",
            ),
        )

    def __str__(self) -> str:
        return f"Booking {self.id} ({self.status})"


class BookingStatusEvent(models.Model):
    """Append-only audit trail: one row per status change, written in the same transaction."""

    booking = models.ForeignKey(
        Booking, on_delete=models.PROTECT, related_name="status_events", db_index=False
    )
    from_status = models.CharField(
        max_length=16, choices=BookingStatus.choices, blank=True, default=""
    )
    to_status = models.CharField(max_length=16, choices=BookingStatus.choices)
    reason = models.CharField(
        max_length=24, choices=BookingStatusReason.choices, blank=True, default=""
    )
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        db_index=False,
    )
    # The payment whose result caused this change, if any.
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        db_index=False,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "booking_status_events"
        ordering = ("created_at", "id")
        indexes = (
            models.Index(fields=("booking", "created_at"), name="booking_events_booking_idx"),
        )
        constraints = (
            models.CheckConstraint(
                condition=Q(to_status__in=BookingStatus.values),
                name="booking_events_to_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(actor_type__in=ActorType.values),
                name="booking_events_actor_type_valid",
            ),
        )

    def __str__(self) -> str:
        return f"{self.from_status or '∅'} → {self.to_status}"
