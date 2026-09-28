from django.contrib import admin

from apps.mockpay.models import MockCharge


@admin.register(MockCharge)
class MockChargeAdmin(admin.ModelAdmin):
    list_display = ("provider_payment_id", "reference", "amount", "method", "status", "created_at")
    list_filter = ("status", "method")
    search_fields = ("provider_payment_id", "reference")
    readonly_fields = tuple(field.name for field in MockCharge._meta.fields)
