"""Send MockPay webhook events by hand: correctly signed, optionally repeated or tampered.

python manage.py send_webhook <payment_id>                     # the charge's own result
python manage.py send_webhook <payment_id> --status succeeded  # force a result
python manage.py send_webhook <payment_id> --times 3           # one event, delivered 3 times
python manage.py send_webhook <payment_id> --amount 1          # amount mismatch
python manage.py send_webhook <payment_id> --bad-signature     # a forged request
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.mockpay.events import build_event, deliver
from apps.mockpay.models import ChargeStatus, MockCharge

STATUSES = {"succeeded": ChargeStatus.SUCCEEDED, "failed": ChargeStatus.FAILED}


class Command(BaseCommand):
    help = "Deliver a signed MockPay webhook event for a payment."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("payment_id", help="Our payment id (the charge reference).")
        parser.add_argument("--status", choices=sorted(STATUSES), help="Override the result.")
        parser.add_argument("--times", type=int, default=1, help="Deliver the same event N times.")
        parser.add_argument("--event-id", help="Reuse an event id (default: a new one).")
        parser.add_argument("--amount", type=int, help="Override the amount in paise.")
        parser.add_argument("--url", default=settings.MOCKPAY_WEBHOOK_URL)
        parser.add_argument(
            "--bad-signature", action="store_true", help="Sign with the wrong secret."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        charge = MockCharge.objects.filter(reference=options["payment_id"]).first()
        if charge is None:
            raise CommandError("MockPay has no charge for that payment id.")
        status = STATUSES.get(options["status"] or "") or ChargeStatus(
            charge.pending_outcome or charge.status
        )
        if status == ChargeStatus.PROCESSING:
            raise CommandError("The charge is still processing; pass --status.")

        event_id, body = build_event(
            charge, status=status, amount=options["amount"], event_id=options["event_id"]
        )
        secret = settings.MOCKPAY_WEBHOOK_SECRET
        if options["bad_signature"]:
            secret = "whsec_" + "Zm9yZ2VkLXNlY3JldA=="  # base64("forged-secret")

        self.stdout.write(f"event {event_id}: {body.decode()}")
        for attempt in range(1, options["times"] + 1):
            response = deliver(event_id=event_id, body=body, url=options["url"], secret=secret)
            self.stdout.write(f"  delivery {attempt}: HTTP {response.status_code} {response.text}")
