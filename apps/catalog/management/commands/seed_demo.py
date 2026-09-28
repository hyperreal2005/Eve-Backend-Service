"""Seed a demo catalogue and two demo accounts (development only).

Idempotent: it creates whatever is missing and never overwrites existing rows, so it is safe to
run on every start of the development stack. The centres are fictional.
"""

from datetime import time
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role, User
from apps.catalog.models import DiagnosticCategory, DiagnosticCentre, DiagnosticTest, Offering

DEMO_ADMIN = ("ops@eve.test", "Ops-Console-2026!", "EVE Operations")
DEMO_PATIENT = ("patient@eve.test", "Patient-Demo-2026!", "Demo Patient")

PATHOLOGY, RADIOLOGY, CARDIOLOGY = (
    DiagnosticCategory.PATHOLOGY,
    DiagnosticCategory.RADIOLOGY,
    DiagnosticCategory.CARDIOLOGY,
)

TESTS: list[tuple[str, str, str, str]] = [
    ("CBC", "Complete Blood Count", PATHOLOGY, "No fasting needed."),
    ("LIPID", "Lipid Profile", PATHOLOGY, "Fast for 10-12 hours; water is fine."),
    ("HBA1C", "HbA1c (Glycated Haemoglobin)", PATHOLOGY, "No fasting needed."),
    ("TFT", "Thyroid Profile (T3, T4, TSH)", PATHOLOGY, "A morning sample is preferred."),
    ("VITD", "Vitamin D (25-OH)", PATHOLOGY, "No fasting needed."),
    ("LFT", "Liver Function Test", PATHOLOGY, "Fast for 8-10 hours."),
    ("KFT", "Kidney Function Test", PATHOLOGY, "No fasting needed."),
    ("XRAY_CHEST", "X-Ray Chest (PA view)", RADIOLOGY, "Remove metal objects and jewellery."),
    (
        "USG_ABDOMEN",
        "Ultrasound Whole Abdomen",
        RADIOLOGY,
        "Fast for 6 hours; drink water an hour before so the bladder is full.",
    ),
    ("CT_CHEST", "CT Chest (HRCT)", RADIOLOGY, "Bring previous reports. Tell staff if pregnant."),
    (
        "MRI_BRAIN",
        "MRI Brain (Plain)",
        RADIOLOGY,
        "Remove all metal. Tell staff about implants or a pacemaker.",
    ),
    ("DEXA", "DEXA Scan (Bone Density)", RADIOLOGY, "Skip calcium supplements for 24 hours."),
    ("MAMMO", "Mammography (Bilateral)", RADIOLOGY, "Avoid deodorant or talc on the day."),
    ("ECG", "Electrocardiogram (ECG)", CARDIOLOGY, "No preparation needed."),
]

# Prices in whole rupees; stored in paise.
CENTRES: list[dict[str, Any]] = [
    {
        "name": "Cyber City Imaging Centre",
        "address_line": "Tower B, DLF Cyber City, Phase 2",
        "city": "Gurugram",
        "state": "Haryana",
        "pincode": "122002",
        "latitude": Decimal("28.494976"),
        "longitude": Decimal("77.089530"),
        "opens_at": time(7, 0),
        "closes_at": time(21, 0),
        "prices": {
            "MRI_BRAIN": 6500,
            "CT_CHEST": 4200,
            "XRAY_CHEST": 450,
            "USG_ABDOMEN": 1500,
            "DEXA": 2400,
            "MAMMO": 2200,
            "CBC": 400,
            "ECG": 400,
        },
    },
    {
        "name": "Sector 29 Diagnostics",
        "address_line": "Plot 14, Sector 29",
        "city": "Gurugram",
        "state": "Haryana",
        "pincode": "122001",
        "opens_at": time(7, 0),
        "closes_at": time(20, 0),
        "prices": {
            "CBC": 350,
            "LIPID": 650,
            "HBA1C": 500,
            "TFT": 550,
            "VITD": 1300,
            "LFT": 750,
            "KFT": 750,
            "XRAY_CHEST": 400,
            "ECG": 350,
        },
    },
    {
        "name": "Rajouri Garden Health Labs",
        "address_line": "J-12, Rajouri Garden Main Market",
        "city": "New Delhi",
        "state": "Delhi",
        "pincode": "110027",
        "opens_at": time(7, 30),
        "closes_at": time(20, 0),
        "prices": {
            "CBC": 380,
            "LIPID": 700,
            "HBA1C": 480,
            "TFT": 600,
            "VITD": 1400,
            "USG_ABDOMEN": 1300,
            "ECG": 300,
        },
    },
    {
        "name": "Indiranagar Scan & Lab",
        "address_line": "100 Feet Road, HAL 2nd Stage, Indiranagar",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": "560038",
        "latitude": Decimal("12.971891"),
        "longitude": Decimal("77.641151"),
        "opens_at": time(6, 30),
        "closes_at": time(22, 0),
        "prices": {
            "MRI_BRAIN": 7200,
            "CT_CHEST": 4800,
            "USG_ABDOMEN": 1700,
            "XRAY_CHEST": 500,
            "CBC": 420,
            "LIPID": 850,
            "VITD": 1600,
            "ECG": 450,
        },
    },
    {
        "name": "Andheri West Diagnostics",
        "address_line": "Link Road, Andheri West",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400053",
        "opens_at": time(7, 0),
        "closes_at": time(21, 0),
        "prices": {
            "MRI_BRAIN": 8000,
            "CT_CHEST": 5200,
            "MAMMO": 2600,
            "DEXA": 2900,
            "CBC": 450,
            "HBA1C": 550,
            "TFT": 700,
            "KFT": 850,
        },
    },
]


class Command(BaseCommand):
    help = "Create the demo catalogue and demo accounts if they don't exist yet."

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        tests = {
            code: DiagnosticTest.objects.get_or_create(
                code=code, defaults={"name": name, "category": category, "preparation": prep}
            )[0]
            for code, name, category, prep in TESTS
        }

        offerings_created = 0
        for data in CENTRES:
            fields = {key: value for key, value in data.items() if key != "prices"}
            centre, _ = DiagnosticCentre.objects.get_or_create(
                name=fields.pop("name"), city=fields.pop("city"), defaults=fields
            )
            for code, rupees in data["prices"].items():
                _, created = Offering.objects.get_or_create(
                    centre=centre, test=tests[code], defaults={"price": rupees * 100}
                )
                offerings_created += created

        for (email, password, full_name), role in (
            (DEMO_ADMIN, Role.ADMIN),
            (DEMO_PATIENT, Role.PATIENT),
        ):
            if not User.objects.filter(email=email).exists():
                if role == Role.ADMIN:
                    User.objects.create_superuser(email, password, full_name=full_name)
                else:
                    User.objects.create_user(email, password, full_name=full_name)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data ready: {len(TESTS)} tests, {len(CENTRES)} centres, "
                f"{offerings_created} new offerings.\n"
                f"  admin:   {DEMO_ADMIN[0]} / {DEMO_ADMIN[1]}\n"
                f"  patient: {DEMO_PATIENT[0]} / {DEMO_PATIENT[1]}"
            )
        )
