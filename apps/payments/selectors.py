from uuid import UUID

from apps.accounts.models import User
from apps.payments.errors import PaymentNotFound
from apps.payments.models import Payment


def payment_get(*, payment_id: UUID, user: User) -> Payment:
    """A payment the caller may see: their own, or any for administrators."""
    payments = Payment.objects.select_related("booking")
    if not user.is_admin:
        payments = payments.filter(booking__user=user)
    try:
        return payments.get(id=payment_id)
    except Payment.DoesNotExist:
        raise PaymentNotFound() from None
