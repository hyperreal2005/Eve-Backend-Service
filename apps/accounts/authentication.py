from typing import Any

import structlog
from rest_framework.request import Request
from rest_framework_simplejwt import authentication


class JWTAuthentication(authentication.JWTAuthentication):
    """simplejwt's bearer-token authentication, plus the caller's id on every log line."""

    def authenticate(self, request: Request) -> tuple[Any, Any] | None:
        result = super().authenticate(request)
        if result is not None:
            structlog.contextvars.bind_contextvars(user_id=str(result[0].pk))
        return result
