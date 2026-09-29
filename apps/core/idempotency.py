"""The `Idempotency-Key` request header: retrying a POST without doing its work twice.

Follows the IETF draft (draft-ietf-httpapi-idempotency-key-header). A client that timed out
repeats the request with the same key and gets back what the first attempt created, in its
current state, instead of a duplicate or a 409. The same key with a different request is a 422.

Keys are stored on the row the request created, under a unique index, so the key and the
resource are written in one INSERT and can never disagree. There is no separate response cache
to keep consistent, and no "in progress" state: a concurrent request with the same key waits on
the key's lock until the first commits, then finds its result.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from rest_framework.request import Request

from apps.core.db import advisory_xact_lock
from apps.core.errors import DomainError, FieldValidationError

HEADER = "Idempotency-Key"

# Visible ASCII except `"` and `\`: what a structured-field string holds without escaping.
_VALID_KEY = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]{1,255}")


class IdempotencyKeyReused(DomainError):
    status_code = 422
    code = "IDEMPOTENCY_KEY_REUSED"
    title = "Idempotency key reused"
    default_detail = (
        "This Idempotency-Key was already used for a different request. "
        "Use a new key for each new request."
    )


@dataclass(frozen=True)
class IdempotencyKey:
    value: str
    fingerprint: str  # identifies the request the key was sent with

    def lock(self, scope: str, owner_id: UUID) -> None:
        """Hold the key until the transaction ends. Call before looking the key up.

        A concurrent request with the same key waits here until the first one commits, then
        finds what it created.
        """
        advisory_xact_lock(f"idempotency:{scope}:{owner_id}:{self.value}")

    def ensure_same_request(self, fingerprint: str) -> None:
        """Called with the fingerprint stored with the key: the same key, another request?"""
        if fingerprint != self.fingerprint:
            raise IdempotencyKeyReused()


def idempotency_key(request: Request, payload: Mapping[str, Any]) -> IdempotencyKey | None:
    """The request's Idempotency-Key, if it sent one, fingerprinted with its validated payload."""
    raw = request.headers.get(HEADER)
    if raw is None:
        return None
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]  # the draft's form is a quoted string ("8e03…"); bare keys work too
    if not _VALID_KEY.fullmatch(value):
        raise FieldValidationError(
            field=HEADER,
            code="invalid",
            message="Use 1-255 visible ASCII characters, such as a UUID.",
        )
    return IdempotencyKey(value=value, fingerprint=fingerprint(payload))


def fingerprint(payload: Mapping[str, Any]) -> str:
    """A hash of what a request means, not how it was written.

    Computed from validated values, so a retry that orders fields differently, or writes the same
    instant with another UTC offset, still counts as the same request.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_canonical)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Can't fingerprint a {type(value).__name__}")
