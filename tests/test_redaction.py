"""Log records never keep an email address or an access token."""

from __future__ import annotations

import logging

from gsc_mcp.redaction import install_redaction, redact_text


def test_redaction_removes_email_and_tokens():
    text = redact_text(
        "user user@example.com bearer Bearer ya29.secretvalue access_token=ya29.other"
    )
    assert "user@example.com" not in text
    assert "ya29.secretvalue" not in text
    assert "ya29.other" not in text
    assert "[redacted]" in text


def test_log_records_are_redacted(caplog):
    install_redaction()
    with caplog.at_level(logging.WARNING):
        logging.getLogger("gsc_mcp.test").warning(
            "sign-in %s token %s",
            "user@example.com",
            "ya29.secretvalue",
        )
    assert "user@example.com" not in caplog.text
    assert "ya29.secretvalue" not in caplog.text
    assert "[redacted]" in caplog.text
