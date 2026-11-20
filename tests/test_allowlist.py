"""Allowlist fails closed and trusts only a verified email."""

from __future__ import annotations

import asyncio
import logging

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken

from gsc_mcp.coordinator import (
    _EmailAllowlistVerifier,
    _allowed_emails_from_env,
    _email_is_allowed,
    _email_is_verified,
)


class _Inner(TokenVerifier):
    def __init__(self, claims: dict | None):
        super().__init__(required_scopes=["openid"])
        self._claims = claims

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == "invalid":
            return None
        return AccessToken(
            token=token,
            client_id="client",
            scopes=["openid"],
            claims=self._claims or {},
        )


def test_empty_allowlist_allows_nobody(monkeypatch):
    monkeypatch.delenv("GSC_MCP_ALLOWED_EMAILS", raising=False)
    assert _allowed_emails_from_env() == frozenset()
    monkeypatch.setenv("GSC_MCP_ALLOWED_EMAILS", "  ,  ")
    assert _allowed_emails_from_env() == frozenset()
    claims = {"email": "user@example.com", "email_verified": True}
    assert _email_is_allowed(claims, frozenset()) is False


def test_allowlist_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(
        "GSC_MCP_ALLOWED_EMAILS",
        " User@Example.com , second@example.com ",
    )
    allowed = _allowed_emails_from_env()
    assert allowed == frozenset({"user@example.com", "second@example.com"})
    claims = {"email": "USER@example.com", "email_verified": "TRUE"}
    assert _email_is_allowed(claims, allowed) is True


def test_unverified_email_is_rejected():
    allowed = frozenset({"user@example.com"})
    for verified in (False, "false", "FALSE", None, "yes", 1, ""):
        claims = {"email": "user@example.com", "email_verified": verified}
        assert _email_is_allowed(claims, allowed) is False
    assert _email_is_verified(True) is True
    assert _email_is_verified(" true ") is True
    assert _email_is_allowed({"email_verified": True}, allowed) is False
    assert _email_is_allowed({"email": 15, "email_verified": True}, allowed) is False
    assert _email_is_allowed(None, allowed) is False


def test_verifier_rejects_without_logging_the_address(caplog):
    verifier = _EmailAllowlistVerifier(
        _Inner({"email": "user@example.com", "email_verified": False}),
        frozenset({"user@example.com"}),
    )
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(verifier.verify_token("token-under-test"))
    assert result is None
    assert "user@example.com" not in caplog.text
    assert "not allowlisted" in caplog.text


def test_verifier_accepts_a_verified_allowlisted_account():
    verifier = _EmailAllowlistVerifier(
        _Inner({"email": "User@Example.com", "email_verified": "true"}),
        frozenset({"user@example.com"}),
    )
    result = asyncio.run(verifier.verify_token("token-under-test"))
    assert result is not None
    assert asyncio.run(verifier.verify_token("invalid")) is None
