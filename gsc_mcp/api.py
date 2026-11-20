"""Read-only HTTP client for searchconsole.googleapis.com."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx2

from gsc_mcp.formatting import compact_inspection, compact_sitemap
from gsc_mcp.limits import BATCH_INSPECT_MAX, BATCH_INSPECT_PAUSE_SECONDS, MAX_ROWS
from gsc_mcp.redaction import redact_text

_HOST = "https://searchconsole.googleapis.com"
_SITES = f"{_HOST}/webmasters/v3/sites"
_INSPECT = f"{_HOST}/v1/urlInspection/index:inspect"
_RETRYABLE = {429, 500, 502, 503, 504}
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_MAX_ATTEMPTS = 4  # one request plus three retries


class GscApiError(Exception):
    """A Search Console call failed after the retry policy was applied."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def site_path(site_url: str) -> str:
    """Encode a property URL for a single path segment."""
    if not isinstance(site_url, str) or not site_url.strip():
        raise ValueError("site_url is required")
    return quote(site_url.strip(), safe="")


def analytics_body(
    *,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None,
    filters: list[dict] | None,
    search_type: str,
    aggregation_type: str,
    data_state: str,
    row_limit: int,
    start_row: int,
) -> dict:
    """JSON body for searchAnalytics.query. Field names match the v1 API."""
    body: dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "type": search_type,
        "dataState": data_state,
        "rowLimit": row_limit,
        "startRow": start_row,
        "aggregationType": aggregation_type,
    }
    if dimensions:
        body["dimensions"] = list(dimensions)
    if filters:
        body["dimensionFilterGroups"] = [
            {
                "groupType": "and",
                "filters": [
                    {
                        "dimension": item["dimension"],
                        "operator": item["operator"],
                        "expression": item["expression"],
                    }
                    for item in filters
                ],
            }
        ]
    return body


def parse_analytics_rows(payload: dict, dimensions: list[str]) -> list[dict]:
    """Turn API rows into dicts with a keys map and raw metrics."""
    parsed = []
    for raw in payload.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        keys_list = raw.get("keys") or []
        keys = {}
        for index, dimension in enumerate(dimensions):
            if index < len(keys_list):
                keys[dimension] = keys_list[index]
        parsed.append(
            {
                "keys": keys,
                "clicks": raw.get("clicks") or 0,
                "impressions": raw.get("impressions") or 0,
                "ctr": raw.get("ctr") or 0,
                "position": raw.get("position") if raw.get("position") is not None else 0,
            }
        )
    return parsed


def _safe_api_message(payload: bytes) -> str:
    text = ""
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            text = error["message"]
    text = redact_text(text).strip()
    if len(text) > 240:
        text = text[:240].rstrip() + "…"
    return text


class SearchConsole:
    """One user's Google access token. The token is never logged or repr'd."""

    def __init__(self, token: str, *, transport=None, sleeper=None) -> None:
        if not isinstance(token, str) or not token:
            raise PermissionError("No Google access token for this session")
        self._token = token
        self._transport = transport
        self._sleep = sleeper or _real_sleep
        self._client: httpx2.AsyncClient | None = None

    def __repr__(self) -> str:
        return "SearchConsole()"

    async def __aenter__(self) -> "SearchConsole":
        self._client = httpx2.AsyncClient(
            transport=self._transport,
            timeout=20.0,
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        return False

    async def list_sites(self) -> list[dict]:
        payload = await self._request("GET", _SITES)
        sites = []
        for entry in payload.get("siteEntry") or []:
            if not isinstance(entry, dict):
                continue
            site_url = entry.get("siteUrl")
            if not isinstance(site_url, str):
                continue
            sites.append(
                {
                    "siteUrl": site_url,
                    "permissionLevel": entry.get("permissionLevel"),
                }
            )
        return sites

    async def search_analytics(
        self,
        site_url: str,
        start_date: str,
        end_date: str,
        *,
        dimensions: list[str] | None = None,
        filters: list[dict] | None = None,
        search_type: str = "web",
        aggregation_type: str = "auto",
        data_state: str = "final",
        max_rows: int = 100,
        start_row: int = 0,
        page_cap: int = MAX_ROWS,
    ) -> tuple[list[dict], bool]:
        """Fetch up to max_rows. truncated is true only when another row exists past the cap."""
        if max_rows < 1 or max_rows > MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {MAX_ROWS}")
        if start_row < 0:
            raise ValueError("start_row must be 0 or greater")
        if page_cap < 1 or page_cap > MAX_ROWS:
            raise ValueError(f"page_cap must be between 1 and {MAX_ROWS}")
        dims = list(dimensions or [])

        async def fetch_page(row_limit: int, row_offset: int) -> list[dict]:
            body = analytics_body(
                start_date=start_date,
                end_date=end_date,
                dimensions=dims,
                filters=filters,
                search_type=search_type,
                aggregation_type=aggregation_type,
                data_state=data_state,
                row_limit=row_limit,
                start_row=row_offset,
            )
            payload = await self._request(
                "POST",
                f"{_SITES}/{site_path(site_url)}/searchAnalytics/query",
                json_body=body,
            )
            return parse_analytics_rows(payload, dims)

        collected: list[dict] = []
        offset = start_row
        remaining = max_rows
        while remaining > 0:
            page_size = min(page_cap, remaining)
            page = await fetch_page(page_size, offset)
            collected.extend(page)
            if len(page) < page_size:
                break
            remaining -= len(page)
            offset += len(page)
        if len(collected) > max_rows:
            return collected[:max_rows], True
        if len(collected) < max_rows:
            return collected, False
        # The cap was filled. One extra row tells us whether the API had more.
        extra = await fetch_page(1, start_row + max_rows)
        return collected[:max_rows], len(extra) > 0

    async def list_sitemaps(self, site_url: str, sitemap_index: str | None = None) -> list[dict]:
        params = {"sitemapIndex": sitemap_index} if sitemap_index else None
        payload = await self._request(
            "GET",
            f"{_SITES}/{site_path(site_url)}/sitemaps",
            params=params,
        )
        return [
            compact_sitemap(item)
            for item in payload.get("sitemap") or []
            if isinstance(item, dict)
        ]

    async def get_sitemap(self, site_url: str, feedpath: str) -> dict:
        if not isinstance(feedpath, str) or not feedpath.strip():
            raise ValueError("feedpath is required")
        payload = await self._request(
            "GET",
            f"{_SITES}/{site_path(site_url)}/sitemaps/{quote(feedpath.strip(), safe='')}",
        )
        return compact_sitemap(payload)

    async def inspect_url(self, site_url: str, inspection_url: str, language_code: str) -> dict:
        if not isinstance(inspection_url, str) or not inspection_url.strip():
            raise ValueError("inspection_url is required")
        payload = await self._request(
            "POST",
            _INSPECT,
            json_body={
                "inspectionUrl": inspection_url.strip(),
                "siteUrl": site_url.strip(),
                "languageCode": language_code,
            },
        )
        compact = compact_inspection(payload)
        compact["inspectionUrl"] = inspection_url.strip()
        return compact

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        if self._client is None:
            raise RuntimeError("SearchConsole must be used as an async context manager")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                )
            except httpx2.HTTPError:
                raise GscApiError(0, "Search Console request failed") from None
            if response.status_code not in _RETRYABLE or attempt >= _MAX_ATTEMPTS - 1:
                return _decode(response)
            delay = _retry_delay(response, attempt)
            await response.aclose()
            await self._sleep(delay)
            attempt += 1


def _retry_delay(response, attempt: int) -> float:
    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
    header = response.headers.get("Retry-After")
    if header:
        try:
            parsed = float(header)
        except ValueError:
            parsed = None
        if parsed is not None and 0 <= parsed <= 30:
            return parsed
    return delay


def _decode(response) -> dict:
    status = response.status_code
    if status == 403:
        raise GscApiError(403, "No access to this Search Console property.")
    if status == 429:
        raise GscApiError(429, "Search Console quota exceeded. Retry later.")
    if status >= 500:
        raise GscApiError(status, f"Search Console temporarily unavailable (HTTP {status}).")
    if status == 401:
        raise GscApiError(401, "Google rejected the access token. Sign in again.")
    if status != 200:
        detail = _safe_api_message(response.content or b"")
        message = f"Search Console rejected the request (HTTP {status})."
        if detail:
            message = f"{message} {detail}"
        raise GscApiError(status, message)
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError:
        raise GscApiError(status, "Search Console returned a response that was not JSON.") from None
    if not isinstance(payload, dict):
        raise GscApiError(status, "Search Console returned an unexpected response.")
    return payload


async def inspect_urls(
    client: SearchConsole,
    site_url: str,
    urls: list[str],
    language_code: str,
    *,
    pause: float = BATCH_INSPECT_PAUSE_SECONDS,
    sleeper=None,
) -> tuple[list[dict], list[dict]]:
    """Inspect URLs one by one. Stop after a quota error and keep earlier results."""
    if not urls:
        raise ValueError("urls must not be empty")
    if len(urls) > BATCH_INSPECT_MAX:
        raise ValueError(f"batch_inspect_urls accepts at most {BATCH_INSPECT_MAX} URLs per call")
    sleep = sleeper or _real_sleep
    results: list[dict] = []
    errors: list[dict] = []
    for index, url in enumerate(urls):
        if index and pause > 0:
            await sleep(pause)
        try:
            results.append(await client.inspect_url(site_url, url, language_code))
        except GscApiError as exc:
            errors.append({"inspectionUrl": url, "message": str(exc)})
            if exc.status == 429:
                for remaining in urls[index + 1 :]:
                    errors.append(
                        {
                            "inspectionUrl": remaining,
                            "message": "Not attempted after quota error.",
                        }
                    )
                break
    return results, errors


async def _real_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
