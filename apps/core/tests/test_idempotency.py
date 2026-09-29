from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.core.errors import FieldValidationError
from apps.core.idempotency import IdempotencyKeyReused, fingerprint, idempotency_key

PAYLOAD = {"booking_id": uuid4(), "payment_method": "mock_success"}


def request_with(key: str | None) -> Request:
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    return Request(APIRequestFactory().post("/", **headers))


def test_no_header_means_no_key():
    assert idempotency_key(request_with(None), PAYLOAD) is None


@pytest.mark.parametrize(
    "header", ["3f2b1c9e-8d4a-4c1e-9b7f-2a6d5e4c3b1a", '"3f2b1c9e-8d4a-4c1e-9b7f-2a6d5e4c3b1a"']
)
def test_a_key_may_be_bare_or_the_drafts_quoted_string(header):
    key = idempotency_key(request_with(header), PAYLOAD)
    assert key is not None
    assert key.value == "3f2b1c9e-8d4a-4c1e-9b7f-2a6d5e4c3b1a"


@pytest.mark.parametrize(
    "header", ["", '""', "has space", "tab\there", "naïve", 'quote"inside', "x" * 256]
)
def test_a_malformed_key_is_rejected(header):
    with pytest.raises(FieldValidationError) as raised:
        idempotency_key(request_with(header), PAYLOAD)
    [error] = raised.value.extra["errors"]
    assert (error["field"], error["code"]) == ("Idempotency-Key", "invalid")


def test_a_key_of_255_characters_is_accepted():
    assert idempotency_key(request_with("k" * 255), PAYLOAD) is not None


def test_the_fingerprint_ignores_how_the_request_was_written():
    ist = timezone(timedelta(hours=5, minutes=30))
    booking = {"centre_id": uuid4(), "appointment_at": datetime(2026, 10, 2, 10, 0, tzinfo=ist)}
    same_instant_in_utc = {**booking, "appointment_at": datetime(2026, 10, 2, 4, 30, tzinfo=UTC)}
    reordered = dict(reversed(list(same_instant_in_utc.items())))
    assert fingerprint(booking) == fingerprint(reordered)
    later = {**booking, "appointment_at": booking["appointment_at"] + timedelta(minutes=15)}
    assert fingerprint(booking) != fingerprint(later)


def test_the_same_key_with_another_request_is_refused():
    key = idempotency_key(request_with("key-1"), PAYLOAD)
    assert key is not None
    key.ensure_same_request(fingerprint(PAYLOAD))
    with pytest.raises(IdempotencyKeyReused):
        key.ensure_same_request(fingerprint({**PAYLOAD, "payment_method": "mock_decline"}))


def test_only_json_values_uuids_and_datetimes_can_be_fingerprinted():
    with pytest.raises(TypeError):
        fingerprint({"when": object()})
