"""MockPay as a PaymentGateway. Test payment methods pick the outcome, like Stripe's test cards."""

import random
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings

from apps.mockpay.models import ChargeStatus, MockCharge
from apps.mockpay.services import charge_create
from apps.payments.gateway import GatewayError, PaymentResult
from apps.payments.models import PaymentStatus


@dataclass(frozen=True)
class Scenario:
    outcome: ChargeStatus  # SUCCEEDED or FAILED
    failure_code: str = ""
    asynchronous: bool = False  # the result arrives later, by webhook (UPI-collect style)
    response_lost: bool = False  # the charge happens, but the caller never hears back


SCENARIOS: dict[str, Scenario] = {
    "mock_success": Scenario(ChargeStatus.SUCCEEDED),
    "mock_decline": Scenario(ChargeStatus.FAILED, "card_declined"),
    "mock_insufficient_funds": Scenario(ChargeStatus.FAILED, "insufficient_funds"),
    "mock_async_success": Scenario(ChargeStatus.SUCCEEDED, asynchronous=True),
    "mock_async_failure": Scenario(ChargeStatus.FAILED, "upi_collect_declined", asynchronous=True),
    "mock_gateway_error": Scenario(ChargeStatus.SUCCEEDED, asynchronous=True, response_lost=True),
}
RANDOM = "mock_random"

_TO_PAYMENT_STATUS = {
    ChargeStatus.PROCESSING: PaymentStatus.PENDING,
    ChargeStatus.SUCCEEDED: PaymentStatus.SUCCESS,
    ChargeStatus.FAILED: PaymentStatus.FAILED,
}


def to_result(charge: MockCharge) -> PaymentResult:
    status = ChargeStatus(charge.status)
    return PaymentResult(
        status=_TO_PAYMENT_STATUS[status],
        provider_payment_id=charge.provider_payment_id,
        # A charge still processing has no failure to report yet, whatever it will settle on.
        failure_code=charge.failure_code if status == ChargeStatus.FAILED else "",
    )


class MockPayGateway:
    name = "mockpay"
    payment_methods = (*SCENARIOS, RANDOM)
    default_method = RANDOM

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()  # noqa: S311 (simulating outcomes, not cryptography)

    def charge(self, *, reference: UUID, amount: int, currency: str, method: str) -> PaymentResult:
        scenario = self._scenario(method)
        charge = charge_create(
            reference=reference,
            amount=amount,
            currency=currency,
            method=method,
            outcome=scenario.outcome,
            failure_code=scenario.failure_code,
            asynchronous=scenario.asynchronous,
        )
        if scenario.response_lost:
            raise GatewayError("simulated timeout: the charge was made but no response arrived")
        return to_result(charge)

    def fetch(self, *, reference: UUID) -> PaymentResult | None:
        charge = MockCharge.objects.filter(reference=reference).first()
        return to_result(charge) if charge else None

    def _scenario(self, method: str) -> Scenario:
        if method == RANDOM:
            success = self._rng.random() < settings.MOCKPAY_SUCCESS_RATE
            return SCENARIOS["mock_success" if success else "mock_decline"]
        return SCENARIOS[method]
