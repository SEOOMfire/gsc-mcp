"""Google user credentials for Search Console calls.

The token is the upstream Google access token FastMCP exposes after the
OAuth proxy validates the session. There is no service-account fallback.
"""

from __future__ import annotations


def google_access_token() -> str:
    """Return the current user's Google access token, or refuse the call."""
    from fastmcp.server.dependencies import get_access_token
    from google.oauth2.credentials import Credentials

    token_obj = get_access_token()
    raw = getattr(token_obj, "token", None) if token_obj is not None else None
    if not isinstance(raw, str) or not raw:
        raise PermissionError("No Google access token for this session")
    credentials = Credentials(token=raw)
    token = credentials.token
    if not isinstance(token, str) or not token:
        raise PermissionError("No Google access token for this session")
    return token
