from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.payments.models import Payment, WebhookEvent


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Financial records change only through the payment services."""

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Payment)
class PaymentAdmin(_ReadOnlyAdmin):
    list_display = ("id", "booking", "amount", "status", "refund_status", "method", "created_at")
    list_filter = ("status", "refund_status", "provider")
    search_fields = ("id", "provider_payment_id", "booking__id")


@admin.register(WebhookEvent)
class WebhookEventAdmin(_ReadOnlyAdmin):
    """REJECTED events are the dead-letter queue: authentic events we couldn't apply."""

    list_display = ("event_id", "event_type", "status", "outcome", "payment", "received_at")
    list_filter = ("status", "event_type", "outcome")
    search_fields = ("event_id", "payment__id")
