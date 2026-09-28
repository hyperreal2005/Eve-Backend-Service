from datetime import time

import factory
from factory.django import DjangoModelFactory

from apps.catalog.models import DiagnosticCategory, DiagnosticCentre, DiagnosticTest, Offering


class DiagnosticCentreFactory(DjangoModelFactory):
    class Meta:
        model = DiagnosticCentre

    name = factory.Sequence(lambda n: f"Centre {n}")
    address_line = factory.Sequence(lambda n: f"{n} MG Road")
    city = "Gurugram"
    state = "Haryana"
    pincode = "122001"
    opens_at = time(7, 0)
    closes_at = time(21, 0)


class DiagnosticTestFactory(DjangoModelFactory):
    class Meta:
        model = DiagnosticTest

    code = factory.Sequence(lambda n: f"TEST_{n}")
    name = factory.Sequence(lambda n: f"Test {n:03d}")
    category = DiagnosticCategory.PATHOLOGY


class OfferingFactory(DjangoModelFactory):
    class Meta:
        model = Offering

    centre = factory.SubFactory(DiagnosticCentreFactory)
    test = factory.SubFactory(DiagnosticTestFactory)
    price = 150_000
