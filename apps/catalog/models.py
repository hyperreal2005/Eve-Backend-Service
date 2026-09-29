"""The catalogue: partner centres, the global test catalogue, and offerings.

An offering is a test available at a centre, at that centre's price: the many-to-many between
centres and tests, with the price living on the association itself.
"""

from typing import ClassVar

from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from apps.core.models import BaseModel

DEFAULT_TIMEZONE = "Asia/Kolkata"


class DiagnosticCategory(models.TextChoices):
    PATHOLOGY = "PATHOLOGY", "Pathology"
    RADIOLOGY = "RADIOLOGY", "Radiology"
    CARDIOLOGY = "CARDIOLOGY", "Cardiology"
    OTHER = "OTHER", "Other"


class DiagnosticCentre(BaseModel):
    """A partner centre where tests are performed."""

    name = models.CharField(max_length=200)
    address_line = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=6)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # Opening hours are wall-clock times in the centre's own time zone.
    timezone = models.CharField(max_length=64, default=DEFAULT_TIMEZONE)
    opens_at = models.TimeField()
    closes_at = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "diagnostic_centres"
        ordering = ("name", "id")
        constraints = (
            # Branches of one brand in one city need distinct names (e.g. by locality).
            models.UniqueConstraint(
                Lower("name"), Lower("city"), name="diagnostic_centres_name_city_uniq"
            ),
            models.CheckConstraint(
                condition=Q(pincode__regex=r"^[1-9][0-9]{5}$"),
                name="diagnostic_centres_pincode_format",
            ),
            models.CheckConstraint(
                condition=Q(opens_at__lt=F("closes_at")), name="diagnostic_centres_hours_valid"
            ),
            models.CheckConstraint(
                condition=Q(latitude__isnull=True) | Q(latitude__gte=-90, latitude__lte=90),
                name="diagnostic_centres_latitude_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__isnull=True) | Q(longitude__gte=-180, longitude__lte=180),
                name="diagnostic_centres_longitude_range",
            ),
        )
        indexes = (
            models.Index(
                Lower("city"), condition=Q(is_active=True), name="diagnostic_centres_city_idx"
            ),
        )

    def __str__(self) -> str:
        return f"{self.name}, {self.city}"


class DiagnosticTest(BaseModel):
    """A test in the global catalogue (e.g. MRI Brain). Centres offer it at their own price."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=16, choices=DiagnosticCategory.choices)
    description = models.TextField(blank=True, default="")
    preparation = models.TextField(
        blank=True, default="", help_text="Instructions for the patient, e.g. fasting."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "diagnostic_tests"
        ordering = ("name", "id")
        constraints = (
            models.UniqueConstraint(fields=("code",), name="diagnostic_tests_code_uniq"),
            models.CheckConstraint(
                condition=Q(code__regex=r"^[A-Z0-9_]{2,32}$"), name="diagnostic_tests_code_format"
            ),
            models.CheckConstraint(
                condition=Q(category__in=DiagnosticCategory.values),
                name="diagnostic_tests_category_valid",
            ),
        )

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class OfferingQuerySet(models.QuerySet["Offering"]):
    def available(self) -> "OfferingQuerySet":
        """Bookable right now: the offering, its test and its centre are all active."""
        return self.filter(is_active=True, test__is_active=True, centre__is_active=True)


class Offering(BaseModel):
    """A test offered at a centre, at that centre's price."""

    # No separate index on centre_id: the (centre, test) unique index already leads with it.
    centre = models.ForeignKey(
        DiagnosticCentre, on_delete=models.PROTECT, related_name="offerings", db_index=False
    )
    test = models.ForeignKey(DiagnosticTest, on_delete=models.PROTECT, related_name="offerings")
    price = models.IntegerField(help_text="In paise: 150000 is ₹1,500.00.")
    currency = models.CharField(max_length=3, default="INR")
    # How many patients one appointment slot can take (an MRI scanner: one). Null means no limit,
    # as for sample collection.
    slot_capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects: ClassVar[OfferingQuerySet] = OfferingQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        db_table = "offerings"
        constraints = (
            models.UniqueConstraint(fields=("centre", "test"), name="offerings_centre_test_uniq"),
            models.CheckConstraint(condition=Q(price__gt=0), name="offerings_price_positive"),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"), name="offerings_currency_format"
            ),
            models.CheckConstraint(
                condition=Q(slot_capacity__isnull=True) | Q(slot_capacity__gt=0),
                name="offerings_slot_capacity_positive",
            ),
        )
        indexes = (
            # "Where is this test offered, cheapest first?"
            models.Index(
                fields=("test", "price"),
                condition=Q(is_active=True),
                name="offerings_test_price_idx",
            ),
        )

    def __str__(self) -> str:
        return f"{self.test} at {self.centre}"
