"""The only OAuth scopes this server is allowed to request."""

from __future__ import annotations

# Canonical forms returned by Google's tokeninfo endpoint.
REQUIRED_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/webmasters.readonly",
)
