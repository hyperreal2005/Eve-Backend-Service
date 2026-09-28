import pytest

from apps.core.standard_webhooks import SignatureError, sign, signed_headers, verify

# The example from the Standard Webhooks reference libraries: a known answer.
SPEC_SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
SPEC_ID, SPEC_TIMESTAMP, SPEC_BODY = (
    "msg_p5jXN8AQM9LWM0D4loKWxJek",
    1614265330,
    b'{"test": 2432232314}',
)
SPEC_SIGNATURE = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="

SECRET = "whsec_dGVzdC1vbmx5LW1vY2twYXktd2ViaG9vay1zZWNyZXQ="
OTHER_SECRET = "whsec_b3RoZXItc2VjcmV0LWZvci1yb3RhdGlvbg=="
NOW = 1_800_000_000
BODY = b'{"type":"payment.succeeded"}'


def headers(secret: str = SECRET, *, timestamp: int = NOW, body: bytes = BODY) -> dict[str, str]:
    return signed_headers(secret, msg_id="evt_1", timestamp=timestamp, body=body)


def check(request_headers, body: bytes = BODY, secrets=(SECRET,)) -> str:
    return verify(request_headers, body, secrets=secrets, now=NOW, tolerance_seconds=300)


def test_signing_matches_the_specifications_example():
    assert sign(SPEC_SECRET, msg_id=SPEC_ID, timestamp=SPEC_TIMESTAMP, body=SPEC_BODY) == (
        SPEC_SIGNATURE
    )


def test_a_valid_signature_returns_the_message_id():
    assert check(headers()) == "evt_1"


@pytest.mark.parametrize(
    "request_headers",
    [
        {},
        {**headers(), "webhook-signature": ""},
        {**headers(), "webhook-timestamp": "yesterday"},
        headers(OTHER_SECRET),  # signed with a secret we don't hold
        headers(timestamp=NOW - 301),  # too old: a captured request being replayed
        headers(timestamp=NOW + 301),  # from the future
        {**headers(), "webhook-signature": headers()["webhook-signature"].replace("v1,", "v2,")},
    ],
)
def test_unverifiable_requests_are_rejected(request_headers):
    with pytest.raises(SignatureError):
        check(request_headers)


def test_the_signature_covers_every_byte_of_the_body():
    with pytest.raises(SignatureError):
        check(headers(), body=BODY.replace(b"succeeded", b"failed"))


def test_the_timestamp_is_signed_too():
    tampered = {**headers(timestamp=NOW - 600), "webhook-timestamp": str(NOW)}
    with pytest.raises(SignatureError):
        check(tampered)


def test_secrets_can_be_rotated_without_downtime():
    # During rotation the receiver accepts both secrets and the sender may send both signatures.
    assert check(headers(OTHER_SECRET), secrets=(SECRET, OTHER_SECRET)) == "evt_1"
    both = f"{headers(OTHER_SECRET)['webhook-signature']} {headers()['webhook-signature']}"
    assert check({**headers(), "webhook-signature": both}) == "evt_1"
