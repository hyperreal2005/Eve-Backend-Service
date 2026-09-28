import uuid

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.bookings.models import BookingStatus
from apps.bookings.tests.factories import BookingFactory
from apps.core.tests.assertions import assert_problem, field_errors
from apps.payments.models import Payment
from apps.payments.tests.factories import PAYMENTS_URL

pytestmark = pytest.mark.django_db


def pay(client, booking, method="mock_success", url=PAYMENTS_URL, **extra):
    return client.post(url, {"booking_id": str(booking.id), "payment_method": method, **extra})


@pytest.fixture
def booking(user):
    return BookingFactory(user=user, amount=650_000)


def test_a_successful_payment_is_created_and_confirms_the_booking(user_client, booking):
    response = pay(user_client, booking)

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["booking_status"]) == ("SUCCESS", "CONFIRMED")
    assert (body["amount"], body["currency"], body["method"]) == (650_000, "INR", "mock_success")
    assert (body["failure_code"], body["refund_status"]) == (None, None)
    assert response["Location"].endswith(f"{PAYMENTS_URL}{body['id']}/")


def test_a_declined_payment_is_still_a_created_resource(user_client, booking):
    body = pay(user_client, booking, "mock_decline").json()
    assert (body["status"], body["failure_code"], body["booking_status"]) == (
        "FAILED",
        "card_declined",
        "FAILED",
    )


@pytest.mark.parametrize("method", ["mock_async_success", "mock_gateway_error"])
def test_a_pending_outcome_is_accepted_not_created(user_client, booking, method):
    response = pay(user_client, booking, method)
    assert response.status_code == 202
    assert (response.json()["status"], response.json()["booking_status"]) == ("PENDING", "PENDING")


def test_the_briefs_literal_path_works_too(user_client, booking):
    assert pay(user_client, booking, url="/payments/").status_code == 201


def test_the_amount_can_not_be_chosen_by_the_client(user_client, booking):
    body = assert_problem(pay(user_client, booking, amount=1), 400, "VALIDATION_ERROR")
    assert ("amount", "unknown_field") in field_errors(body)
    assert not Payment.objects.exists()


def test_the_payment_method_must_be_one_the_provider_offers(user_client, booking):
    body = assert_problem(pay(user_client, booking, "bitcoin"), 400, "VALIDATION_ERROR")
    assert ("payment_method", "invalid_choice") in field_errors(body)


def test_invalid_and_foreign_booking_ids(user_client):
    malformed = user_client.post(PAYMENTS_URL, {"booking_id": "123"})
    assert ("booking_id", "invalid") in field_errors(
        assert_problem(malformed, 400, "VALIDATION_ERROR")
    )
    unknown = user_client.post(PAYMENTS_URL, {"booking_id": str(uuid.uuid4())})
    assert_problem(unknown, 404, "BOOKING_NOT_FOUND")
    someone_elses = BookingFactory(user=UserFactory())
    assert_problem(pay(user_client, someone_elses), 404, "BOOKING_NOT_FOUND")


def test_paying_twice_is_a_conflict(user_client, booking):
    pay(user_client, booking)
    assert_problem(pay(user_client, booking), 409, "BOOKING_ALREADY_PAID")
    assert Payment.objects.count() == 1


def test_a_failed_booking_needs_a_new_booking_not_a_retry(user_client, booking):
    pay(user_client, booking, "mock_decline")
    assert_problem(pay(user_client, booking), 409, "BOOKING_NOT_PAYABLE")


def test_paying_requires_authentication(api_client, booking):
    assert_problem(pay(api_client, booking), 401, "AUTHENTICATION_REQUIRED")


def test_reading_a_payment_is_limited_to_its_owner_and_administrators(
    user_client, admin_client, booking
):
    payment_id = pay(user_client, booking).json()["id"]
    assert user_client.get(f"{PAYMENTS_URL}{payment_id}/").json()["status"] == "SUCCESS"
    assert admin_client.get(f"{PAYMENTS_URL}{payment_id}/").status_code == 200

    stranger = BookingFactory(user=UserFactory())
    foreign = Payment.objects.create(
        booking=stranger,
        amount=stranger.amount,
        currency="INR",
        method="mock_success",
        provider="mockpay",
    )
    assert_problem(user_client.get(f"{PAYMENTS_URL}{foreign.id}/"), 404, "PAYMENT_NOT_FOUND")


def test_the_booking_shows_its_payment_attempts_and_what_changed_it(user_client, booking):
    payment_id = pay(user_client, booking).json()["id"]
    body = user_client.get(f"/api/v1/bookings/{booking.id}/").json()

    assert [(p["id"], p["status"]) for p in body["payments"]] == [(payment_id, "SUCCESS")]
    assert [(h["to_status"], h["payment_id"]) for h in body["history"]] == [
        (BookingStatus.CONFIRMED, payment_id)
    ]


def test_cancelling_during_a_payment_is_refused(user_client, booking):
    pay(user_client, booking, "mock_async_success")
    response = user_client.post(f"/api/v1/bookings/{booking.id}/cancel/")
    assert_problem(response, 409, "PAYMENT_IN_PROGRESS")
