from typing import Any

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request


def is_admin(user: Any) -> bool:
    return bool(user and user.is_authenticated and getattr(user, "is_admin", False))


class IsAdminRole(BasePermission):
    message = "This action requires an administrator."

    def has_permission(self, request: Request, view: Any) -> bool:
        return is_admin(request.user)


class IsAdminOrReadOnly(BasePermission):
    """Anyone may read; only administrators may write (the public catalogue)."""

    message = "Only administrators can change the catalogue."

    def has_permission(self, request: Request, view: Any) -> bool:
        return request.method in SAFE_METHODS or is_admin(request.user)
