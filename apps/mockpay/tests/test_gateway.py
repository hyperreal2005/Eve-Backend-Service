import random
from uuid import uuid4

import pytest

from apps.mockpay.gateway import SCENARIOS, MockPayGateway, to_result
from apps.mockpay.models import ChargeStatus, MockCharge
from apps.mockpay.services import charge_settle
from apps.payments.gateway import GatewayError
from apps.payments.models import PaymentStatus

pytestmark = pytest.mark.django_db


def charge(method: str, gateway: MockPayGateway | None = None):
    return (gateway or MockPayGateway()).charge(
        reference=uuid4(), amount=45_000, currency="INR", method=method
    )


@pytest.mark.parametrize(
    ("method", "status", "failure_code"),
    [
        ("mock_success", PaymentStatus.SUCCESS, ""),
        ("mock_decline", PaymentStatus.FAILED, "card_declined"),
        ("mock_insufficient_funds", PaymentStatus.FAILED, "insufficient_funds"),
        ("mock_async_success", PaymentStatus.PENDING, ""),
        ("mock_async_failure", PaymentStatus.PENDING, ""),
    ],
)
def test_each_test_method_produces_its_documented_result(method, status, failure_code):
    result = charge(method)
    assert (result.status, result.failure_code) == (status, failure_code)
    assert result.provider_payment_id.startswith("mockpay_pay_")


def test_a_lost_response_still_leaves_a_charge_at_the_provider():
    gateway, reference = MockPayGateway(), uuid4()
    with pytest.raises(GatewayError):
        gateway.charge(reference=reference, amount=100, currency="INR", method="mock_gateway_error")
    assert gateway.fetch(reference=reference).status == PaymentStatus.PENDING


def test_asynchronous_charges_settle_on_the_outcome_chosen_at_charge_time():
    for method, expected in (
        ("mock_async_success", ChargeStatus.SUCCEEDED),
        ("mock_async_failure", ChargeStatus.FAILED),
    ):
        result = charge(method)
        record = MockCharge.objects.get(provider_payment_id=result.provider_payment_id)
        assert charge_settle(charge_id=record.id).status == expected
        assert charge_settle(charge_id=record.id).status == expected  # settling again: no change


def seeded(seed: int) -> MockPayGateway:
    return MockPayGateway(rng=random.Random(seed))  # noqa: S311 (simulation, not cryptography)


def test_the_default_method_is_random_but_reproducible_with_a_seed():
    outcomes = {charge("mock_random", seeded(seed)).status for seed in range(20)}
    assert outcomes == {PaymentStatus.SUCCESS, PaymentStatus.FAILED}
    assert charge("mock_random", seeded(7)).status == charge("mock_random", seeded(7)).status


def test_fetch_knows_nothing_about_a_charge_it_never_made():
    assert MockPayGateway().fetch(reference=uuid4()) is None


def test_every_advertised_method_is_implemented():
    assert set(MockPayGateway.payment_methods) == {*SCENARIOS, "mock_random"}
    record = MockCharge.objects.create(
        reference=uuid4(),
        provider_payment_id="mockpay_pay_x",
        amount=1,
        currency="INR",
        method="mock_success",
        status=ChargeStatus.SUCCEEDED,
    )
    assert to_result(record).status == PaymentStatus.SUCCESS
