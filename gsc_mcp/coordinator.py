# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastMCP server, Google OAuth proxy, and email allowlist.

Adapted from the Google Ads MCP. The allowlist reads GoogleProvider's
``_token_validator`` and ``_code_store``. Those names are private; a test
fails if a FastMCP upgrade removes them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")

from gsc_mcp.redaction import install_redaction

install_redaction()

from fastmcp import FastMCP
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from mcp import types as mcp_types
from mcp.server.auth.provider import TokenError
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler

from gsc_mcp.auth_storage import create_client_storage
from gsc_mcp.scopes import REQUIRED_SCOPES

logger = logging.getLogger(__name__)

ALLOWED_EMAILS_ENV = "GSC_MCP_ALLOWED_EMAILS"
OAUTH_ENV = (
    "GSC_MCP_CLIENT_ID",
    "GSC_MCP_CLIENT_SECRET",
    "GSC_MCP_BASE_URL",
    "GSC_MCP_JWT_SIGNING_KEY",
)
RUNTIME_ENV = OAUTH_ENV + (
    "GSC_MCP_STORAGE_ENCRYPTION_KEY",
    "GSC_MCP_STORAGE_PATH",
)
# GoogleProvider merges this over its own access_type=offline and prompt=consent.
# "false" keeps the token to the scopes of this authorization, so a shared
# OAuth client does not also return previously granted Ads scopes.
EXTRA_AUTHORIZE_PARAMS = {"include_granted_scopes": "false"}
INSTRUCTIONS = (
    "Read-only Google Search Console. "
    "Country codes are ISO 3166-1 alpha-3 in lowercase, for example deu. "
    "If start_date or end_date is omitted, the range is the 28 days ending on "
    "the last day with final data. The dates actually queried are in meta."
)


def _allowed_emails_from_env() -> frozenset[str]:
    """Comma-separated allowlist. Empty or unset means nobody is allowed."""
    raw = os.environ.get(ALLOWED_EMAILS_ENV, "")
    return frozenset(piece.strip().casefold() for piece in raw.split(",") if piece.strip())


def _email_is_verified(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().casefold() == "true"


def _email_is_allowed(claims: dict[str, Any] | None, allowed: frozenset[str]) -> bool:
    if not allowed or not claims:
        return False
    email = claims.get("email")
    if not isinstance(email, str) or not _email_is_verified(claims.get("email_verified")):
        return False
    return email.strip().casefold() in allowed


class _EmailAllowlistVerifier(TokenVerifier):
    """Reject Google tokens whose verified email is not on the allowlist."""

    def __init__(self, inner: TokenVerifier, allowed_emails: frozenset[str]):
        super().__init__(required_scopes=list(inner.required_scopes or []))
        self._inner = inner
        self._allowed_emails = allowed_emails

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await self._inner.verify_token(token)
        if access is None:
            return None
        if not _email_is_allowed(access.claims, self._allowed_emails):
            logger.warning("Rejected Google sign-in: account is not allowlisted")
            return None
        return access


class _AllowlistedGoogleProvider(GoogleProvider):
    """GoogleProvider that refuses every account unless the allowlist permits it."""

    def __init__(self, *, allowed_emails: frozenset[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        inner = getattr(self, "_token_validator", None)
        if inner is None or not hasattr(inner, "verify_token"):
            raise RuntimeError("Refusing to start OAuth without an email allowlist hook")
        if not hasattr(self, "_code_store"):
            raise RuntimeError("Refusing to start OAuth without an authorization-code store")
        self._allowed_emails = allowed_emails
        self._token_validator = _EmailAllowlistVerifier(inner, allowed_emails)
        if not allowed_emails:
            logger.warning(
                "%s is empty or unset; OAuth sign-in is disabled",
                ALLOWED_EMAILS_ENV,
            )

    async def exchange_authorization_code(
        self, client: Any, authorization_code: Any
    ) -> Any:
        code_store = getattr(self, "_code_store", None)
        code = getattr(authorization_code, "code", None)
        code_model = None
        if code_store is not None and isinstance(code, str):
            code_model = await code_store.get(key=code)
        idp_tokens = getattr(code_model, "idp_tokens", None)
        access_token = idp_tokens.get("access_token") if isinstance(idp_tokens, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise TokenError("access_denied", "Google account is not allowed")
        if await self._token_validator.verify_token(access_token) is None:
            raise TokenError("access_denied", "Google account is not allowed")
        return await super().exchange_authorization_code(client, authorization_code)


@dataclass(frozen=True)
class OAuthSettings:
    """OAuth settings. ``repr`` hides every value."""

    client_id: str
    client_secret: str
    base_url: str
    jwt_signing_key: str
    allowed_emails: frozenset[str]

    def __repr__(self) -> str:
        return "OAuthSettings()"


def load_oauth_settings() -> OAuthSettings | None:
    """Return settings when OAuth is fully configured.

    All-or-nothing: a partial set of OAuth variables is a startup error.
    No variables means the process is importing for tests and auth stays off.
    """
    values = {name: os.environ.get(name, "").strip() for name in OAUTH_ENV}
    present = [name for name, value in values.items() if value]
    if present and len(present) != len(values):
        missing = [name for name, value in values.items() if not value]
        raise RuntimeError("Incomplete OAuth configuration: " + ", ".join(missing))
    if not present:
        return None
    if not os.environ.get("GSC_MCP_STORAGE_PATH", "").strip():
        raise RuntimeError("GSC_MCP_STORAGE_PATH is required")
    if not os.environ.get("GSC_MCP_STORAGE_ENCRYPTION_KEY", "").strip():
        raise RuntimeError("GSC_MCP_STORAGE_ENCRYPTION_KEY is required")
    return OAuthSettings(
        client_id=values["GSC_MCP_CLIENT_ID"],
        client_secret=values["GSC_MCP_CLIENT_SECRET"],
        base_url=values["GSC_MCP_BASE_URL"],
        jwt_signing_key=values["GSC_MCP_JWT_SIGNING_KEY"],
        allowed_emails=_allowed_emails_from_env(),
    )


def missing_runtime_env() -> list[str]:
    """Names of required runtime variables that are empty or unset."""
    return [name for name in RUNTIME_ENV if not os.environ.get(name, "").strip()]


def build_provider(
    *,
    client_id: str,
    client_secret: str,
    base_url: str,
    jwt_signing_key: str,
    allowed_emails: frozenset[str],
    client_storage: Any,
) -> _AllowlistedGoogleProvider:
    """Construct the production OAuth provider."""
    return _AllowlistedGoogleProvider(
        allowed_emails=allowed_emails,
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        required_scopes=list(REQUIRED_SCOPES),
        jwt_signing_key=jwt_signing_key,
        client_storage=client_storage,
        extra_authorize_params=dict(EXTRA_AUTHORIZE_PARAMS),
    )


def ensure_subscriptions_listen(server: FastMCP) -> bool:
    """Register the MCP subscription stream when FastMCP omits it.

    FastMCP 4.1 builds its low-level server without the SDK's
    ``subscriptions/listen`` handler. Some clients open that stream while
    loading tools. The guard does nothing once FastMCP ships the handler.
    """
    low_level_server = server._mcp_server
    if "subscriptions/listen" in low_level_server._request_handlers:
        return False
    low_level_server.add_request_handler(
        "subscriptions/listen",
        mcp_types.SubscriptionsListenRequestParams,
        ListenHandler(InMemorySubscriptionBus()),
    )
    return True


_settings = load_oauth_settings()
if _settings is None:
    mcp = FastMCP("Google Search Console", instructions=INSTRUCTIONS)
else:
    mcp = FastMCP(
        "Google Search Console",
        instructions=INSTRUCTIONS,
        auth=build_provider(
            client_id=_settings.client_id,
            client_secret=_settings.client_secret,
            base_url=_settings.base_url,
            jwt_signing_key=_settings.jwt_signing_key,
            allowed_emails=_settings.allowed_emails,
            client_storage=create_client_storage(),
        ),
    )

ensure_subscriptions_listen(mcp)

# Imported last so tool modules can bind to ``mcp``.
from gsc_mcp import tools as _tools  # noqa: E402,F401
