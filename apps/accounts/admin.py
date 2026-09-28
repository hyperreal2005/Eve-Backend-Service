from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from apps.accounts.models import User, normalize_email


class _NormalisedEmailMixin(forms.ModelForm):
    def clean_email(self) -> str:
        return normalize_email(self.cleaned_data["email"])


class UserCreationForm(_NormalisedEmailMixin, BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "full_name", "role")


class UserUpdateForm(_NormalisedEmailMixin, UserChangeForm):
    class Meta:
        model = User
        fields = ("email", "full_name", "phone", "role", "is_active")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserUpdateForm
    add_form = UserCreationForm
    ordering = ("-created_at",)
    list_display = ("email", "full_name", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("email", "full_name")
    readonly_fields = ("id", "created_at", "last_login")
    filter_horizontal = ()
    fieldsets = (
        (None, {"fields": ("id", "email", "password")}),
        ("Profile", {"fields": ("full_name", "phone")}),
        ("Access", {"fields": ("role", "is_active")}),
        ("Activity", {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "role", "password1", "password2"),
            },
        ),
    )
