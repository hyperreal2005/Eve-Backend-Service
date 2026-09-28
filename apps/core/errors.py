"""Domain errors: business-rule failures with a stable, machine-readable code.

Services raise these; `problem_details.exception_handler` renders them as RFC 9457 responses.
Views never build error responses by hand.
"""

from typing import Any, ClassVar


class DomainError(Exception):
    status_code: ClassVar[int] = 400
    code: ClassVar[str] = "BAD_REQUEST"
    title: ClassVar[str] = "Bad request"
    default_detail: ClassVar[str] = "The request could not be processed."
    # Sent with 401s: how the client should authenticate.
    www_authenticate: ClassVar[str] = 'Bearer realm="eve-diagnostics"'

    def __init__(self, detail: str | None = None, **extra: Any) -> None:
        self.detail = detail or self.default_detail
        # Extra members of the problem body, e.g. {"booking_id": "..."}; must be JSON-serialisable.
        self.extra = extra
        super().__init__(self.detail)


class FieldValidationError(DomainError):
    """A business rule about one input field, rendered exactly like a serializer error.

    For rules that need the database (e.g. a centre's opening hours), so they can't live in the
    serializer, but should still look like any other 400 to the client.
    """

    code = "VALIDATION_ERROR"
    title = "Invalid request"

    def __init__(self, *, field: str, code: str, message: str) -> None:
        super().__init__(
            "One or more fields are invalid.",
            errors=[{"field": field, "code": code, "message": message}],
        )


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"
    title = "Not found"
    default_detail = "The requested resource does not exist."


class Conflict(DomainError):
    status_code = 409
    code = "CONFLICT"
    title = "Conflict"
    default_detail = "The request conflicts with the current state of the resource."


class InvalidStateTransition(Conflict):
    code = "INVALID_STATE_TRANSITION"
    title = "Invalid state transition"
    default_detail = "This action is not allowed in the resource's current state."
