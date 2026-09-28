"""The database refuses inconsistent payments, whatever path the write takes."""

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payments.models import Payment, PaymentStatus, RefundStatus
from apps.payments.tests.factories import PaymentFactory

pytestmark = pytest.mark.django_db


def assert_rejected(write) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        write()


def test_at_most_one_live_payment_per_booking():
    payment = PaymentFactory()
    assert_rejected(lambda: PaymentFactory(booking=payment.booking))
    assert_rejected(lambda: PaymentFactory(booking=payment.booking, status=PaymentStatus.SUCCESS))
    # Failed attempts don't count.
    assert PaymentFactory(booking=payment.booking, status=PaymentStatus.FAILED)


def test_provider_payment_ids_are_unique_when_known():
    payment = PaymentFactory()
    assert_rejected(lambda: PaymentFactory(provider_payment_id=payment.provider_payment_id))
    # Unknown ids (not yet reported by the provider) don't collide.
    PaymentFactory(provider_payment_id="")
    PaymentFactory(provider_payment_id="")
    assert Payment.objects.filter(provider_payment_id="").count() == 2


def test_a_finished_payment_carries_its_completion_time():
    payment = PaymentFactory()
    assert_rejected(
        lambda: Payment.objects.filter(pk=payment.pk).update(status=PaymentStatus.SUCCESS)
    )


def test_only_a_captured_payment_can_owe_a_refund():
    failed = PaymentFactory(status=PaymentStatus.FAILED)
    assert_rejected(
        lambda: Payment.objects.filter(pk=failed.pk).update(refund_status=RefundStatus.PENDING)
    )
    paid = PaymentFactory(status=PaymentStatus.SUCCESS, completed_at=timezone.now())
    Payment.objects.filter(pk=paid.pk).update(refund_status=RefundStatus.PENDING)


def test_amount_must_be_positive():
    payment = PaymentFactory()
    assert_rejected(lambda: Payment.objects.filter(pk=payment.pk).update(amount=0))
