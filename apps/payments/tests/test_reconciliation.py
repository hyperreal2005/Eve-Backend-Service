"""Reconciliation: asking the provider about payments stuck in PENDING."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.bookings.models import ActorType, BookingStatus
from apps.mockpay.models import ChargeStatus, MockCharge
from apps.payments.models import PaymentStatus
from apps.payments.tasks import reconcile_stale_payments
from apps.payments.tests.factories import PaymentFactory

pytestmark = pytest.mark.django_db


def stuck_payment(**overrides):
    """A payment still PENDING five minutes on: its webhook never arrived."""
    return PaymentFactory(created_at=timezone.now() - timedelta(minutes=5), **overrides)


def provider_charge(payment, status: ChargeStatus) -> MockCharge:
    return MockCharge.objects.create(
        reference=payment.id,
        provider_payment_id=payment.provider_payment_id,
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        status=status,
        failure_code="card_declined" if status == ChargeStatus.FAILED else "",
    )


def reload(payment):
    payment.refresh_from_db()
    payment.booking.refresh_from_db()
    return payment.status, payment.booking.status


def test_a_charge_the_provider_completed_is_applied():
    payment = stuck_payment()
    provider_charge(payment, ChargeStatus.SUCCEEDED)

    assert reconcile_stale_payments() == {"applied": 1}
    assert reload(payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)
    event = payment.booking.status_events.get()
    assert (event.actor_type, event.payment_id) == (ActorType.SYSTEM, payment.id)


def test_a_charge_the_provider_declined_is_applied():
    payment = stuck_payment()
    provider_charge(payment, ChargeStatus.FAILED)
    reconcile_stale_payments()
    assert reload(payment) == (PaymentStatus.FAILED, BookingStatus.FAILED)
    assert payment.failure_code == "card_declined"


def test_a_charge_still_processing_is_left_for_next_time():
    payment = stuck_payment()
    provider_charge(payment, ChargeStatus.PROCESSING)
    assert reconcile_stale_payments() == {"still_pending": 1}
    assert reload(payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


def test_a_charge_the_provider_never_saw_counts_as_failed():
    # We crashed between recording the payment and charging: the provider's answer is the fact.
    payment = stuck_payment()
    reconcile_stale_payments()
    assert reload(payment) == (PaymentStatus.FAILED, BookingStatus.FAILED)
    assert payment.failure_code == "not_found_at_provider"


def test_recent_payments_are_given_time_for_their_webhook():
    payment = PaymentFactory()  # created just now
    provider_charge(payment, ChargeStatus.SUCCEEDED)
    assert reconcile_stale_payments() == {}
    assert reload(payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


def test_an_unreachable_provider_changes_nothing(settings):
    settings.PAYMENT_GATEWAY = "apps.payments.tests.fakes.UnreachableGateway"
    payment = stuck_payment()
    assert reconcile_stale_payments() == {"provider_unreachable": 1}
    assert reload(payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)
