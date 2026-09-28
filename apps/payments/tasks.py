from celery import shared_task
from django.conf import settings

from apps.payments.services import payment_reconcile_stale


@shared_task(name="payments.reconcile_stale_payments")
def reconcile_stale_payments() -> dict[str, int]:
    return payment_reconcile_stale(older_than=settings.PAYMENT_RECONCILE_AFTER)
