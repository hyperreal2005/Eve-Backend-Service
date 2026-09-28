"""Webhook signing and verification per the Standard Webhooks specification.

    webhook-id:        unique message id (the receiver's idempotency key)
    webhook-timestamp: unix seconds when this delivery attempt was signed
    webhook-signature: space-separated "v1,<base64 HMAC-SHA256>" entries

The signed content is "{id}.{timestamp}.{raw body}". Several signatures may be sent (and several
secrets accepted) so secrets can be rotated without downtime. Secrets look like "whsec_<base64>".
See https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md
"""

import base64
import hashlib
import hmac
from collections.abc import Iterable, Mapping

SECRET_PREFIX = "whsec_"  # noqa: S105 (a format marker, not a secret)
ID_HEADER = "webhook-id"
TIMESTAMP_HEADER = "webhook-timestamp"
SIGNATURE_HEADER = "webhook-signature"


class SignatureError(Exception):
    """The request can't be proven to come from the provider (or is too old to trust)."""


def _key(secret: str) -> bytes:
    return base64.b64decode(secret.removeprefix(SECRET_PREFIX))


def sign(secret: str, *, msg_id: str, timestamp: int, body: bytes) -> str:
    """The `webhook-signature` header value for one delivery attempt."""
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(_key(secret), signed, hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode()}"


def signed_headers(secret: str, *, msg_id: str, timestamp: int, body: bytes) -> dict[str, str]:
    return {
        ID_HEADER: msg_id,
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(secret, msg_id=msg_id, timestamp=timestamp, body=body),
    }


def verify(
    headers: Mapping[str, str],
    body: bytes,
    *,
    secrets: Iterable[str],
    now: int,
    tolerance_seconds: int,
) -> str:
    """Return the message id if a signature matches one of `secrets`; raise SignatureError.

    `body` must be the raw bytes received: re-serialised JSON would not match. The timestamp is
    part of the signed content, so rejecting old ones stops a captured request being replayed.
    """
    msg_id = headers.get(ID_HEADER, "")
    timestamp = headers.get(TIMESTAMP_HEADER, "")
    signatures = headers.get(SIGNATURE_HEADER, "")
    if not (msg_id and timestamp and signatures):
        raise SignatureError("missing webhook-id, webhook-timestamp or webhook-signature header")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise SignatureError("webhook-timestamp is not an integer") from exc
    if abs(now - sent_at) > tolerance_seconds:
        raise SignatureError("webhook-timestamp is outside the tolerance window")

    expected = {sign(secret, msg_id=msg_id, timestamp=sent_at, body=body) for secret in secrets}
    for candidate in signatures.split():
        # Only v1 counts; anything else is ignored, so an attacker can't downgrade the scheme.
        if candidate.startswith("v1,") and any(
            hmac.compare_digest(candidate, signature) for signature in expected
        ):
            return msg_id
    raise SignatureError("no signature matches")
