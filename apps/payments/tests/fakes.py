from uuid import UUID

from apps.mockpay.gateway import MockPayGateway
from apps.payments.gateway import GatewayError, PaymentResult


class UnreachableGateway(MockPayGateway):
    """A provider whose status API is down."""

    def fetch(self, *, reference: UUID) -> PaymentResult | None:
        raise GatewayError("provider unreachable")
