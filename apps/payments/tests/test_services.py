from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.tests.factories import UserFactory
from apps.bookings.errors import BookingNotFound
from apps.bookings.models import ActorType, BookingStatus, BookingStatusReason
from apps.bookings.services import booking_cancel
from apps.bookings.tests.factories import BookingFactory
from apps.mockpay.models import MockCharge
from apps.payments.domain import Outcome
from apps.payments.errors import (
    BookingAlreadyPaid,
    BookingExpired,
    BookingNotPayable,
    PaymentInProgress,
    ProviderResultRejected,
)
from apps.payments.gateway import PaymentResult
from apps.payments.models import Payment, PaymentStatus, RefundStatus
from apps.payments.services import Source, payment_apply_result, payment_initiate
from apps.payments.tests.factories import PaymentFactory

pytestmark = pytest.mark.django_db


def pay(booking, method="mock_success"):
    return payment_initiate(user=booking.user, booking_id=booking.id, method=method)


def refreshed(*objects):
    for obj in objects:
        obj.refresh_from_db()
    return objects


# ------------------------------------------------------------------------ paying a booking


def test_a_successful_payment_confirms_the_booking():
    booking = BookingFactory(amount=650_000)

    payment = pay(booking)

    (booking,) = refreshed(booking)
    assert (payment.status, payment.amount, payment.currency) == ("SUCCESS", 650_000, "INR")
    assert payment.provider_payment_id.startswith("mockpay_pay_")
    assert payment.completed_at is not None
    assert booking.status == BookingStatus.CONFIRMED
    event = booking.status_events.get(to_status=BookingStatus.CONFIRMED)
    assert (event.actor_type, event.payment_id) == (ActorType.PROVIDER, payment.id)


@pytest.mark.parametrize(
    ("method", "failure_code"),
    [("mock_decline", "card_declined"), ("mock_insufficient_funds", "insufficient_funds")],
)
def test_a_declined_payment_fails_the_booking(method, failure_code):
    booking = BookingFactory()
    payment = pay(booking, method)
    (booking,) = refreshed(booking)
    assert (payment.status, payment.failure_code) == ("FAILED", failure_code)
    assert (booking.status, booking.status_reason) == ("FAILED", "PAYMENT_DECLINED")


def test_an_asynchronous_payment_stays_pending_until_the_provider_reports():
    booking = BookingFactory()
    payment = pay(booking, "mock_async_success")
    (booking,) = refreshed(booking)
    assert (payment.status, booking.status) == ("PENDING", "PENDING")
    assert payment.provider_payment_id  # known already: the provider accepted the charge


def test_an_unreachable_provider_leaves_the_outcome_unknown_not_failed():
    booking = BookingFactory()
    payment = pay(booking, "mock_gateway_error")
    (booking,) = refreshed(booking)
    assert (payment.status, payment.provider_payment_id, booking.status) == (
        "PENDING",
        "",
        "PENDING",
    )
    # The provider did make the charge; the webhook or reconciliation will report it.
    assert MockCharge.objects.filter(reference=payment.id).exists()


def test_the_amount_always_comes_from_the_booking():
    booking = BookingFactory(amount=123_400)
    assert pay(booking).amount == 123_400


def test_only_the_patient_can_pay_for_their_booking():
    booking = BookingFactory()
    for someone_else in (UserFactory(), UserFactory(role=Role.ADMIN)):
        with pytest.raises(BookingNotFound):
            payment_initiate(user=someone_else, booking_id=booking.id, method="mock_success")
    assert not Payment.objects.exists()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (BookingStatus.CONFIRMED, BookingAlreadyPaid),
        (BookingStatus.FAILED, BookingNotPayable),
        (BookingStatus.CANCELLED, BookingNotPayable),
    ],
)
def test_only_pending_bookings_are_payable(status, error):
    with pytest.raises(error):
        pay(BookingFactory(status=status))


def test_a_second_payment_while_one_is_in_flight_is_refused():
    booking = BookingFactory()
    pay(booking, "mock_async_success")
    with pytest.raises(PaymentInProgress):
        pay(booking)
    assert booking.payments.count() == 1


def test_an_expired_hold_fails_the_booking_even_before_the_sweeper_runs():
    booking = BookingFactory(hold_expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(BookingExpired):
        pay(booking)
    (booking,) = refreshed(booking)
    assert (booking.status, booking.status_reason) == ("FAILED", "PAYMENT_TIMEOUT")
    assert not booking.payments.exists()


def test_a_payment_in_flight_keeps_the_hold_alive():
    booking = BookingFactory(hold_expires_at=timezone.now() - timedelta(seconds=1))
    PaymentFactory(booking=booking)
    with pytest.raises(PaymentInProgress):
        pay(booking)
    (booking,) = refreshed(booking)
    assert booking.status == BookingStatus.PENDING


def test_charging_the_same_reference_twice_is_idempotent_at_the_provider():
    from apps.mockpay.gateway import MockPayGateway

    gateway, reference = MockPayGateway(), uuid4()
    first = gateway.charge(reference=reference, amount=100, currency="INR", method="mock_success")
    second = gateway.charge(reference=reference, amount=100, currency="INR", method="mock_decline")
    assert first == second
    assert MockCharge.objects.count() == 1


# ------------------------------------------------------------- applying provider results


def apply(payment, status, source=Source.WEBHOOK, **result):
    return payment_apply_result(
        payment_id=payment.id, result=PaymentResult(status=status, **result), source=source
    )


def test_the_same_result_twice_changes_nothing_the_second_time():
    payment = PaymentFactory()
    assert apply(payment, PaymentStatus.SUCCESS) == Outcome.APPLIED
    assert apply(payment, PaymentStatus.SUCCESS) == Outcome.NOOP
    assert payment.booking.status_events.filter(to_status="CONFIRMED").count() == 1


def test_a_failure_arriving_after_success_is_ignored():
    payment = PaymentFactory()
    apply(payment, PaymentStatus.SUCCESS)
    assert apply(payment, PaymentStatus.FAILED) == Outcome.STALE
    payment, booking = refreshed(payment, payment.booking)
    assert (payment.status, booking.status) == ("SUCCESS", "CONFIRMED")


def test_a_late_success_after_a_decline_is_recorded_and_owed_back():
    payment = PaymentFactory()
    apply(payment, PaymentStatus.FAILED, failure_code="card_declined")

    assert apply(payment, PaymentStatus.SUCCESS, source=Source.RECONCILIATION) == (
        Outcome.REFUND_OWED
    )

    payment, booking = refreshed(payment, payment.booking)
    assert (payment.status, payment.refund_status, payment.failure_code) == (
        "SUCCESS",
        "PENDING",
        "",
    )
    assert booking.status == BookingStatus.FAILED  # a failed booking is never resurrected


def test_a_payment_for_a_cancelled_booking_is_owed_back():
    payment = PaymentFactory(booking=BookingFactory(status=BookingStatus.CANCELLED))
    assert apply(payment, PaymentStatus.SUCCESS) == Outcome.REFUND_OWED
    (payment,) = refreshed(payment)
    assert payment.refund_status == RefundStatus.PENDING


@pytest.mark.parametrize(("delta", "currency"), [(-1, "INR"), (0, "USD")])
def test_a_result_for_a_different_amount_is_rejected_and_changes_nothing(delta, currency):
    payment = PaymentFactory()
    with pytest.raises(ProviderResultRejected) as caught:
        payment_apply_result(
            payment_id=payment.id,
            result=PaymentResult(status=PaymentStatus.SUCCESS),
            source=Source.WEBHOOK,
            expected_amount=payment.amount + delta,
            expected_currency=currency,
        )
    assert caught.value.reason == "amount_mismatch"
    payment, booking = refreshed(payment, payment.booking)
    assert (payment.status, booking.status) == ("PENDING", "PENDING")


def test_a_result_naming_another_provider_payment_is_rejected():
    payment = PaymentFactory(provider_payment_id="mockpay_pay_original")
    with pytest.raises(ProviderResultRejected) as caught:
        apply(payment, PaymentStatus.SUCCESS, provider_payment_id="mockpay_pay_someone_else")
    assert caught.value.reason == "provider_payment_id_mismatch"


def test_a_result_for_an_unknown_payment_is_rejected():
    with pytest.raises(ProviderResultRejected) as caught:
        payment_apply_result(
            payment_id=uuid4(),
            result=PaymentResult(status=PaymentStatus.SUCCESS),
            source=Source.WEBHOOK,
        )
    assert caught.value.reason == "unknown_payment"


# ----------------------------------------------------------- cancelling around payments


def test_a_booking_cannot_be_cancelled_while_its_payment_is_in_flight():
    payment = PaymentFactory()
    with pytest.raises(PaymentInProgress):
        booking_cancel(booking_id=payment.booking_id, user=payment.booking.user)


def test_cancelling_a_paid_booking_records_the_refund_owed():
    booking = BookingFactory(appointment_at=timezone.now() + timedelta(days=2))
    payment = pay(booking)

    booking_cancel(booking_id=booking.id, user=booking.user)

    payment, booking = refreshed(payment, booking)
    assert (booking.status, booking.status_reason) == (
        "CANCELLED",
        BookingStatusReason.USER_CANCELLED,
    )
    assert (payment.status, payment.refund_status) == ("SUCCESS", RefundStatus.PENDING)
