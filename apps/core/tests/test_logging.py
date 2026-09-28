from apps.core.logging import mask_email, redact_sensitive


def test_secrets_are_redacted_whatever_the_key_case():
    event = {"event": "x", "password": "hunter2", "Authorization": "Bearer abc", "refresh": "t"}
    redacted = redact_sensitive(None, "info", event)
    assert redacted == {
        "event": "x",
        "password": "[REDACTED]",
        "Authorization": "[REDACTED]",
        "refresh": "[REDACTED]",
    }


def test_emails_are_masked_not_dropped():
    assert redact_sensitive(None, "info", {"email": "asha@example.com"}) == {
        "email": "a***@example.com"
    }
    assert mask_email("not-an-email") == "***"
