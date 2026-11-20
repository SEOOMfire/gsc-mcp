"""FastMCP 4.1.8 still exposes the private hooks the allowlist uses."""

from __future__ import annotations

from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.memory import MemoryStore

from gsc_mcp.coordinator import EXTRA_AUTHORIZE_PARAMS, _EmailAllowlistVerifier, build_provider
from gsc_mcp.scopes import REQUIRED_SCOPES

_CLIENT_ID = "dummy-client-id"
_CLIENT_SECRET = "dummy-client-secret"
_BASE_URL = "https://gsc-mcp.example.com"
_SIGNING_KEY = "dummy-signing-key-0123456789"


def _provider(**extra):
    return GoogleProvider(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        base_url=_BASE_URL,
        required_scopes=list(REQUIRED_SCOPES),
        jwt_signing_key=_SIGNING_KEY,
        client_storage=MemoryStore(),
        **extra,
    )


def test_google_provider_exposes_allowlist_hooks():
    provider = _provider()
    assert hasattr(provider, "_token_validator")
    assert hasattr(provider, "_code_store")
    assert callable(getattr(provider._token_validator, "verify_token", None))
    assert callable(getattr(provider._code_store, "get", None))


def test_production_provider_limits_granted_scopes_and_keeps_the_hooks():
    provider = build_provider(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        base_url=_BASE_URL,
        jwt_signing_key=_SIGNING_KEY,
        allowed_emails=frozenset({"user@example.com"}),
        client_storage=MemoryStore(),
    )
    assert isinstance(provider, GoogleProvider)
    assert isinstance(provider._token_validator, _EmailAllowlistVerifier)
    assert hasattr(provider, "_code_store")
    assert provider._extra_authorize_params["include_granted_scopes"] == "false"
    assert EXTRA_AUTHORIZE_PARAMS["include_granted_scopes"] == "false"
    assert provider._extra_authorize_params["access_type"] == "offline"
    assert provider._extra_authorize_params["prompt"] == "consent"
    assert set(provider._token_validator.required_scopes) == set(REQUIRED_SCOPES)
    assert "https://www.googleapis.com/auth/webmasters" not in provider._token_validator.required_scopes
