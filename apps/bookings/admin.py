from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.bookings.models import Booking, BookingStatusEvent


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Bookings change only through the state machine; the admin site is for inspection."""

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class BookingStatusEventInline(admin.TabularInline):
    model = BookingStatusEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "from_status", "to_status", "reason", "actor_type", "actor")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Booking)
class BookingAdmin(_ReadOnlyAdmin):
    list_display = ("id", "user", "offering", "appointment_at", "amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("id", "user__email")
    list_select_related = ("user", "offering__centre", "offering__test")
    inlines = (BookingStatusEventInline,)
