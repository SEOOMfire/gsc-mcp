"""The server stays read-only in scopes, source, and tool annotations."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from gsc_mcp.coordinator import mcp
from gsc_mcp.scopes import REQUIRED_SCOPES

PACKAGE = Path(__file__).resolve().parents[1] / "gsc_mcp"
EXPECTED_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/webmasters.readonly",
)
WRITE_SCOPE_RE = re.compile(
    r"https://www\.googleapis\.com/auth/webmasters(?!\.readonly)"
)
FORBIDDEN_SNIPPETS = (
    "sites.add",
    "sites.delete",
    "sitemaps.submit",
    "sitemaps.delete",
    "urlNotifications",
    "indexing.googleapis.com",
    "https://www.googleapis.com/auth/indexing",
)
EXPECTED_TOOLS = {
    "list_sites",
    "find_site",
    "search_analytics",
    "list_sitemaps",
    "get_sitemap",
    "inspect_url",
    "batch_inspect_urls",
    "data_freshness",
    "performance_overview",
    "compare_periods",
    "quick_wins",
    "cannibalization",
    "content_decay",
    "new_and_lost_queries",
    "brand_split",
    "query_page_matrix",
    "portfolio_overview",
    "get_capabilities",
}


def _sources() -> list[Path]:
    return sorted(path for path in PACKAGE.rglob("*.py") if path.is_file())


def test_configured_scopes_are_exactly_the_read_only_set():
    assert REQUIRED_SCOPES == EXPECTED_SCOPES
    assert "https://www.googleapis.com/auth/webmasters" not in REQUIRED_SCOPES


def test_write_scope_pattern_ignores_the_readonly_scope():
    readonly = "https://www.googleapis.com/auth/webmasters.readonly"
    write = "https://www.googleapis.com/auth/webmasters"
    assert WRITE_SCOPE_RE.search(readonly) is None
    assert WRITE_SCOPE_RE.search(readonly + " trailing") is None
    assert WRITE_SCOPE_RE.search(write)
    assert WRITE_SCOPE_RE.search(f'"{write}"')
    assert WRITE_SCOPE_RE.search(write + "/extra")


def test_package_source_has_no_write_scope_or_write_methods():
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        match = WRITE_SCOPE_RE.search(text)
        assert match is None, f"{path.name} contains the write scope"
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet not in text, f"{path.name} contains {snippet}"


def test_every_tool_is_read_only():
    tools = asyncio.run(mcp.list_tools(run_middleware=False))
    names = {tool.name for tool in tools}
    assert names == EXPECTED_TOOLS
    for tool in tools:
        dumped = tool.annotations.model_dump(by_alias=True)
        assert dumped["readOnlyHint"] is True
        assert (tool.description or "").strip()
