"""Tool descriptions expose date defaults and country codes."""

from __future__ import annotations

import asyncio
import json

from gsc_mcp.coordinator import INSTRUCTIONS, mcp


def _dump(name: str) -> str:
    tools = asyncio.run(mcp.list_tools(run_middleware=False))
    tool = next(item for item in tools if item.name == name)
    payload = tool.to_mcp_tool().model_dump(by_alias=True)
    return json.dumps(payload, ensure_ascii=False)


def test_server_instructions_mention_the_country_code_and_the_default_window():
    assert "deu" in INSTRUCTIONS
    assert "28 days" in INSTRUCTIONS
    assert "deu" in mcp.instructions


def test_search_analytics_schema_documents_countries_and_dates():
    text = _dump("search_analytics")
    assert "deu" in text
    assert "28 days" in text
    assert "includingRegex" in text
    assert "googleNews" in text


def test_performance_overview_mentions_country_codes():
    assert "deu" in _dump("performance_overview")


def test_quick_wins_names_the_generic_curve():
    assert "Schätzung (generische Kurve)" in _dump("quick_wins")


def test_portfolio_mentions_parallel_progress():
    text = _dump("portfolio_overview")
    assert "five at a time" in text
    assert "errors" in text
