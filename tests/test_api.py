"""HTTP client: errors, retries, paging, and URL encoding. No network."""

from __future__ import annotations

import json

import httpx2
import pytest

from gsc_mcp.api import GscApiError, SearchConsole, analytics_body, inspect_urls


def _client(handler, sleeper=None) -> SearchConsole:
    return SearchConsole(
        "test-token",
        transport=httpx2.MockTransport(handler),
        sleeper=sleeper,
    )


def test_403_is_not_retried_and_hides_response_secrets():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx2.Response(
            403,
            json={"error": {"message": "denied ya29.secretvalue user@example.com"}},
        )

    async def run():
        async with _client(handler) as client:
            await client.list_sites()

    with pytest.raises(GscApiError) as caught:
        import asyncio

        asyncio.run(run())
    assert caught.value.status == 403
    assert calls["n"] == 1
    assert "No access" in str(caught.value)
    assert "ya29.secretvalue" not in str(caught.value)
    assert "user@example.com" not in str(caught.value)


def test_429_uses_retry_after_then_succeeds():
    calls = {"n": 0}
    delays = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx2.Response(429, headers={"Retry-After": "1.5"}, json={})
        return httpx2.Response(200, json={"siteEntry": [{"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}]})

    async def sleeper(seconds):
        delays.append(seconds)

    async def run():
        async with _client(handler, sleeper) as client:
            return await client.list_sites()

    import asyncio

    sites = asyncio.run(run())
    assert calls["n"] == 2
    assert delays == [1.5]
    assert sites == [{"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}]


def test_server_errors_retry_and_then_report_quota():
    calls = {"n": 0}
    delays = []

    def handler(request):
        calls["n"] += 1
        return httpx2.Response(503, json={"error": {"message": "ya29.secretvalue"}})

    async def sleeper(seconds):
        delays.append(seconds)

    async def run():
        async with _client(handler, sleeper) as client:
            await client.list_sites()

    import asyncio

    with pytest.raises(GscApiError) as caught:
        asyncio.run(run())
    assert calls["n"] == 4
    assert delays == [0.5, 1.0, 2.0]
    assert "ya29.secretvalue" not in str(caught.value)
    assert "503" in str(caught.value)


def test_search_analytics_encodes_the_property_and_pages():
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        start = body["startRow"]
        size = body["rowLimit"]
        rows = [
            {"keys": ["a"], "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 2.2},
            {"keys": ["b"], "clicks": 2, "impressions": 20, "ctr": 0.1, "position": 3.2},
            {"keys": ["c"], "clicks": 3, "impressions": 30, "ctr": 0.1, "position": 4.2},
        ]
        assert body["type"] == "web"
        assert body["dimensionFilterGroups"][0]["filters"][0]["operator"] == "contains"
        return httpx2.Response(200, json={"rows": rows[start : start + size]})

    async def run():
        async with _client(handler) as client:
            return await client.search_analytics(
                "https://www.example.com/",
                "2026-10-20",
                "2026-11-16",
                dimensions=["query"],
                filters=[{"dimension": "query", "operator": "contains", "expression": "example"}],
                max_rows=5,
                page_cap=2,
            )

    import asyncio

    rows, truncated = asyncio.run(run())
    assert truncated is False
    assert [row["keys"]["query"] for row in rows] == ["a", "b", "c"]
    assert len(seen) == 2
    target = str(seen[0].url)
    assert "https%3A%2F%2Fwww.example.com%2F" in target
    assert "/searchAnalytics/query" in target


def test_full_page_at_the_cap_is_truncated():
    def handler(request):
        body = json.loads(request.content)
        rows = [
            {"keys": ["a"], "clicks": 1, "impressions": 1, "ctr": 1, "position": 1},
            {"keys": ["b"], "clicks": 1, "impressions": 1, "ctr": 1, "position": 1},
        ]
        start = body["startRow"]
        size = body["rowLimit"]
        return httpx2.Response(200, json={"rows": rows[start : start + size]})

    async def run():
        async with _client(handler) as client:
            return await client.search_analytics(
                "sc-domain:example.com",
                "2026-10-20",
                "2026-11-16",
                dimensions=["query"],
                max_rows=2,
                page_cap=2,
            )

    import asyncio

    rows, truncated = asyncio.run(run())
    assert len(rows) == 2
    assert truncated is True


def test_batch_inspect_stops_on_quota_and_rejects_a_long_list():
    class Fake:
        def __init__(self):
            self.calls = []

        async def inspect_url(self, site, url, language):
            self.calls.append(url)
            if url.endswith("/quota"):
                raise GscApiError(429, "Search Console quota exceeded. Retry later.")
            return {"inspectionUrl": url}

    async def run():
        fake = Fake()
        results, errors = await inspect_urls(
            fake,
            "sc-domain:example.com",
            [
                "https://www.example.com/a",
                "https://www.example.com/quota",
                "https://www.example.com/c",
            ],
            "en-US",
            pause=0.25,
            sleeper=lambda _seconds: _asleep(),
        )
        return fake, results, errors

    async def _asleep():
        return None

    import asyncio

    fake, results, errors = asyncio.run(run())
    assert fake.calls == [
        "https://www.example.com/a",
        "https://www.example.com/quota",
    ]
    assert results == [{"inspectionUrl": "https://www.example.com/a"}]
    assert errors[1]["message"] == "Not attempted after quota error."

    async def too_many():
        await inspect_urls(Fake(), "sc-domain:example.com", ["https://www.example.com/"] * 11, "en-US")

    with pytest.raises(ValueError):
        asyncio.run(too_many())


def test_analytics_body_uses_the_api_field_names():
    body = analytics_body(
        start_date="2026-10-20",
        end_date="2026-11-16",
        dimensions=["country"],
        filters=[{"dimension": "country", "operator": "equals", "expression": "deu"}],
        search_type="web",
        aggregation_type="auto",
        data_state="final",
        row_limit=100,
        start_row=0,
    )
    assert body["type"] == "web"
    assert body["dataState"] == "final"
    assert "searchType" not in body
    assert body["dimensionFilterGroups"][0]["groupType"] == "and"
