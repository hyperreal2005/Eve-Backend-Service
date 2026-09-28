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

    def __init__(self, detail: str | None = None, **extra: Any) -> None:
        self.detail = detail or self.default_detail
        # Extra members of the problem body, e.g. {"booking_id": "..."}; must be JSON-serialisable.
        self.extra = extra
        super().__init__(self.detail)


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
