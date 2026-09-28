from typing import Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import BaseModel


def normalize_email(email: str) -> str:
    """Emails are compared case-insensitively; the stored form is trimmed and lower-cased."""
    return email.strip().lower()


class Role(models.TextChoices):
    PATIENT = "PATIENT", "Patient"
    ADMIN = "ADMIN", "Admin"


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **fields: Any) -> "User":
        fields.setdefault("role", Role.PATIENT)
        user = self.model(email=normalize_email(email), **fields)
        user.set_password(password)  # None gives an unusable password
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **fields: Any) -> "User":
        fields.update(role=Role.ADMIN, is_superuser=True)
        return self.create_user(email, password, **fields)

    def get_by_natural_key(self, username: str | None) -> "User":
        return self.get(email=normalize_email(username or ""))


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    """A person who can log in: a patient, or an EVE operations administrator."""

    email = models.EmailField(max_length=254)
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=16, blank=True, default="")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.PATIENT)
    is_active = models.BooleanField(default=True)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["full_name"]

    class Meta:
        db_table = "users"
        ordering = ("created_at",)
        constraints = (
            models.UniqueConstraint(fields=("email",), name="users_email_uniq"),
            # The stored email is always the canonical lower-case form, even for writes that
            # bypass the manager, so uniqueness is effectively case-insensitive.
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")), name="users_email_lowercase"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values), name="users_role_valid"
            ),
        )

    def __str__(self) -> str:
        return self.email

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_staff(self) -> bool:
        """Django admin access follows the role: one source of truth for "administrator"."""
        return self.is_admin
