from django.contrib import admin

from apps.catalog.models import DiagnosticCentre, DiagnosticTest, Offering


class OfferingInline(admin.TabularInline):
    model = Offering
    extra = 0
    fields = ("test", "price", "currency", "is_active")
    autocomplete_fields = ("test",)


@admin.register(DiagnosticCentre)
class DiagnosticCentreAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "opens_at", "closes_at", "is_active")
    list_filter = ("is_active", "city")
    search_fields = ("name", "city", "pincode")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = (OfferingInline,)


@admin.register(DiagnosticTest)
class DiagnosticTestAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Offering)
class OfferingAdmin(admin.ModelAdmin):
    list_display = ("test", "centre", "price", "currency", "is_active")
    list_filter = ("is_active", "test__category")
    search_fields = ("centre__name", "test__name", "test__code")
    list_select_related = ("centre", "test")
    autocomplete_fields = ("centre", "test")
    readonly_fields = ("id", "created_at", "updated_at")
