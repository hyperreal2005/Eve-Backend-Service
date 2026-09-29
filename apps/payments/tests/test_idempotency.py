"""Retrying POST /payments/ with an Idempotency-Key: never a second charge."""

import pytest

from apps.accounts.tests.factories import UserFactory, bearer
from apps.bookings.models import BookingStatus
from apps.bookings.tests.factories import BookingFactory
from apps.core.tests.assertions import assert_problem
from apps.mockpay.models import MockCharge
from apps.payments.models import Payment
from apps.payments.tests.factories import PAYMENTS_URL

pytestmark = pytest.mark.django_db

KEY = "0b6e2f8a-3c5d-4e7f-9a1b-2c3d4e5f6a7b"


def pay(client, booking, method="mock_success", key=KEY, url=PAYMENTS_URL):
    payload = {"booking_id": str(booking.id)}
    if method is not None:
        payload["payment_method"] = method
    return client.post(url, payload, HTTP_IDEMPOTENCY_KEY=key)


@pytest.fixture
def booking(user):
    return BookingFactory(user=user)


def test_a_retry_returns_the_original_payment_without_charging_again(user_client, booking):
    first = pay(user_client, booking)
    retry = pay(user_client, booking)

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json()["id"] == first.json()["id"]
    assert (Payment.objects.count(), MockCharge.objects.count()) == (1, 1)


def test_a_retry_after_a_decline_returns_the_declined_payment(user_client, booking):
    """Without the key, the retry would be refused: the declined booking is no longer payable."""
    first = pay(user_client, booking, "mock_decline")
    retry = pay(user_client, booking, "mock_decline")
    assert retry.status_code == 201
    assert (retry.json()["id"], retry.json()["status"]) == (first.json()["id"], "FAILED")


def test_a_retry_finishes_a_payment_the_first_attempt_could_not_charge(
    user_client, booking, settings
):
    settings.PAYMENT_GATEWAY = "apps.payments.tests.fakes.DownGateway"
    first = pay(user_client, booking)
    assert (first.status_code, first.json()["status"]) == (202, "PENDING")
    assert not MockCharge.objects.exists()

    settings.PAYMENT_GATEWAY = "apps.mockpay.gateway.MockPayGateway"
    retry = pay(user_client, booking)

    assert retry.status_code == 201
    assert (retry.json()["id"], retry.json()["status"]) == (first.json()["id"], "SUCCESS")
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert MockCharge.objects.count() == 1


def test_a_retry_of_a_payment_still_processing_does_not_charge_twice(user_client, booking):
    first = pay(user_client, booking, "mock_async_success")
    retry = pay(user_client, booking, "mock_async_success")
    assert (first.status_code, retry.status_code) == (202, 202)
    assert retry.json()["id"] == first.json()["id"]
    assert MockCharge.objects.count() == 1


def test_the_default_method_omitted_or_named_is_the_same_request(user_client, booking, settings):
    settings.MOCKPAY_SUCCESS_RATE = 1.0
    first = pay(user_client, booking, method=None)
    retry = pay(user_client, booking, method="mock_random")
    assert retry.json()["id"] == first.json()["id"]


def test_the_brief_path_and_the_versioned_path_are_the_same_endpoint(user_client, booking):
    first = pay(user_client, booking, url="/payments/")
    retry = pay(user_client, booking)
    assert retry.json()["id"] == first.json()["id"]


def test_the_same_key_for_another_booking_is_a_422(user_client, user, booking):
    pay(user_client, booking)
    response = pay(user_client, BookingFactory(user=user))
    assert_problem(response, 422, "IDEMPOTENCY_KEY_REUSED")
    assert Payment.objects.count() == 1


def test_a_key_cannot_reach_another_patients_payment(api_client, user_client, booking):
    pay(user_client, booking)
    api_client.credentials(HTTP_AUTHORIZATION=bearer(UserFactory()))
    assert_problem(pay(api_client, booking), 404, "BOOKING_NOT_FOUND")
