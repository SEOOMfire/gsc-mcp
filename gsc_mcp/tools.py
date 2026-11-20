"""Read-only Search Console tools."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from gsc_mcp.analysis import (
    EXPECTED_CTR_BASIS,
    brand_split as compute_brand_split,
    cannibalization as compute_cannibalization,
    compare_periods as compute_compare_periods,
    content_decay as compute_content_decay,
    matrix_request,
    new_and_lost_queries as compute_new_and_lost,
    performance_overview as compute_performance_overview,
    quick_wins as compute_quick_wins,
)
from gsc_mcp.api import GscApiError, SearchConsole, inspect_urls
from gsc_mcp.concurrency import gather_limited
from gsc_mcp.coordinator import mcp
from gsc_mcp.credentials import google_access_token
from gsc_mcp.dates import (
    parse_date,
    pick_last_final_day,
    require_range,
    resolve_explicit_or_default,
)
from gsc_mcp.formatting import meta, present_metrics, present_row
from gsc_mcp.limits import (
    BATCH_INSPECT_MAX,
    DEFAULT_ANALYSIS_ROWS,
    DEFAULT_RESULT_LIMIT,
    DEFAULT_ROWS,
    FINAL_LAG_FALLBACK_DAYS,
    FINAL_LOOKBACK_DAYS,
    FRESHNESS_DEFAULT_LOOKBACK_DAYS,
    FRESHNESS_MAX_LOOKBACK_DAYS,
    MAX_ROWS,
    PORTFOLIO_CONCURRENCY,
    PORTFOLIO_DEFAULT_LIMIT,
    PORTFOLIO_MAX_LIMIT,
)
from gsc_mcp.redaction import redact_text

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
LOCAL_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

SITE_HELP = (
    "Exact Search Console property, for example sc-domain:example.com "
    "or https://www.example.com/."
)
DATE_HELP = (
    "YYYY-MM-DD. If start_date or end_date is omitted, both dates are replaced "
    "by the 28 days ending on the last day with final data. "
    "meta.startDate and meta.endDate are the dates actually queried."
)
COUNTRY_HELP = "Country codes are ISO 3166-1 alpha-3 in lowercase, for example deu."
SEARCH_TYPE_HELP = "web, image, video, news, discover, or googleNews."
DATA_STATE_HELP = "final for settled data, or all to include fresh unsettled data."
MAX_ROWS_HELP = (
    f"Maximum rows to fetch, from 1 to {MAX_ROWS}. "
    "The server pages through the API until this many rows are collected."
)
LIMIT_HELP = "Maximum rows to return after filtering."

SearchType = Literal["web", "image", "video", "news", "discover", "googleNews"]
DataState = Literal["final", "all"]
DimensionName = Literal["date", "query", "page", "country", "device", "searchAppearance"]
AggregationType = Literal["auto", "byPage", "byProperty", "byNewsShowcasePanel"]


class DimensionFilter(BaseModel):
    """One AND filter. Combine several filters to narrow a query."""

    dimension: DimensionName = Field(
        description="date, query, page, country, device, or searchAppearance. " + COUNTRY_HELP
    )
    operator: Literal[
        "equals",
        "notEquals",
        "contains",
        "notContains",
        "includingRegex",
        "excludingRegex",
    ] = Field(
        description="equals, notEquals, contains, notContains, includingRegex, or excludingRegex."
    )
    expression: str = Field(
        description="Filter value. For country, an ISO 3166-1 alpha-3 code in lowercase, for example deu."
    )


def _filters(filters: list[DimensionFilter] | None) -> list[dict] | None:
    if not filters:
        return None
    return [item.model_dump() for item in filters]


def _client() -> SearchConsole:
    return SearchConsole(google_access_token())


async def _resolved_range(
    client: SearchConsole,
    site_url: str,
    start_date: str | None,
    end_date: str | None,
    search_type: str,
) -> tuple[str, str]:
    """Use both dates, or the 28 days ending on the last final day."""
    if start_date and end_date:
        return resolve_explicit_or_default(start_date, end_date, last_final=date.today())
    today = date.today()
    rows, _truncated = await client.search_analytics(
        site_url,
        (today - timedelta(days=FINAL_LOOKBACK_DAYS)).isoformat(),
        today.isoformat(),
        dimensions=["date"],
        search_type=search_type,
        data_state="final",
        max_rows=FINAL_LOOKBACK_DAYS + 2,
    )
    last_final = pick_last_final_day(rows, today=today)
    return resolve_explicit_or_default(None, None, last_final=last_final)


def _fallback_range() -> tuple[str, str]:
    last_final = date.today() - timedelta(days=FINAL_LAG_FALLBACK_DAYS)
    return resolve_explicit_or_default(None, None, last_final=last_final)


@mcp.tool(annotations=READ_ONLY)
async def list_sites() -> dict:
    """List every Search Console property this Google account can read, with its permission level."""
    async with _client() as client:
        sites = await client.list_sites()
    return {
        "meta": meta(
            property=None,
            start_date=None,
            end_date=None,
            search_type=None,
            data_state=None,
            row_count=len(sites),
            truncated=False,
        ),
        "sites": sites,
    }


@mcp.tool(annotations=READ_ONLY)
async def find_site(
    query: Annotated[
        str,
        Field(description="Case-insensitive fragment of a property URL or domain, for example example."),
    ],
) -> dict:
    """Find properties whose URL contains the fragment, including sc-domain and URL-prefix properties."""
    needle = query.strip().casefold()
    if not needle:
        raise ValueError("query is required")
    async with _client() as client:
        sites = await client.list_sites()
    matches = [site for site in sites if needle in site["siteUrl"].casefold()]
    return {
        "meta": meta(
            property=None,
            start_date=None,
            end_date=None,
            search_type=None,
            data_state=None,
            row_count=len(matches),
            truncated=False,
        ),
        "sites": matches,
    }


@mcp.tool(annotations=READ_ONLY)
async def search_analytics(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    start_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    end_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    dimensions: Annotated[
        list[DimensionName] | None,
        Field(description="date, query, page, country, device, and/or searchAppearance. " + COUNTRY_HELP),
    ] = None,
    filters: Annotated[
        list[DimensionFilter] | None,
        Field(description="AND filters. " + COUNTRY_HELP),
    ] = None,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    aggregation_type: Annotated[
        AggregationType,
        Field(description="auto, byPage, byProperty, or byNewsShowcasePanel."),
    ] = "auto",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ROWS,
    start_row: Annotated[int, Field(ge=0, description="Row offset into the result, default 0.")] = 0,
) -> dict:
    """Query Search Console performance rows.

    Country codes are ISO 3166-1 alpha-3 in lowercase, for example deu.
    If start_date or end_date is omitted, the query uses the 28 days ending on the last day with final data.
    """
    async with _client() as client:
        used_start, used_end = await _resolved_range(
            client, site_url, start_date, end_date, search_type
        )
        rows, truncated = await client.search_analytics(
            site_url,
            used_start,
            used_end,
            dimensions=list(dimensions or []),
            filters=_filters(filters),
            search_type=search_type,
            aggregation_type=aggregation_type,
            data_state=data_state,
            max_rows=max_rows,
            start_row=start_row,
        )
    return {
        "meta": meta(
            property=site_url,
            start_date=used_start,
            end_date=used_end,
            search_type=search_type,
            data_state=data_state,
            row_count=len(rows),
            truncated=truncated,
        ),
        "rows": [present_row(row) for row in rows],
    }


@mcp.tool(annotations=READ_ONLY)
async def list_sitemaps(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    sitemap_index: Annotated[
        str | None,
        Field(description="Optional parent sitemap index URL. Omit to list the property's sitemaps."),
    ] = None,
) -> dict:
    """List sitemaps with status, errors, warnings, and submitted and indexed URL counts."""
    async with _client() as client:
        sitemaps = await client.list_sitemaps(site_url, sitemap_index)
    return {
        "meta": meta(
            property=site_url,
            start_date=None,
            end_date=None,
            search_type=None,
            data_state=None,
            row_count=len(sitemaps),
            truncated=False,
        ),
        "sitemaps": sitemaps,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_sitemap(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    feedpath: Annotated[str, Field(description="Sitemap URL, for example https://www.example.com/sitemap.xml.")],
) -> dict:
    """Get one sitemap, including errors, warnings, and submitted and indexed URL counts."""
    async with _client() as client:
        sitemap = await client.get_sitemap(site_url, feedpath)
    return {
        "meta": meta(
            property=site_url,
            start_date=None,
            end_date=None,
            search_type=None,
            data_state=None,
            row_count=1,
            truncated=False,
        ),
        "sitemap": sitemap,
    }


@mcp.tool(annotations=READ_ONLY)
async def inspect_url(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    inspection_url: Annotated[str, Field(description="Fully qualified URL to inspect, on this property.")],
    language_code: Annotated[str, Field(description="Language for issue messages, default en-US.")] = "en-US",
) -> dict:
    """Inspect one URL: index status, canonicals, crawl, mobile usability, and rich results."""
    async with _client() as client:
        inspected = await client.inspect_url(site_url, inspection_url, language_code)
    return {
        "meta": meta(
            property=site_url,
            start_date=None,
            end_date=None,
            search_type=None,
            data_state=None,
            row_count=1,
            truncated=False,
        ),
        "inspection": inspected,
    }


@mcp.tool(annotations=READ_ONLY)
async def batch_inspect_urls(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    urls: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=BATCH_INSPECT_MAX,
            description=f"Up to {BATCH_INSPECT_MAX} fully qualified URLs. Inspected one after another.",
        ),
    ],
    language_code: Annotated[str, Field(description="Language for issue messages, default en-US.")] = "en-US",
) -> dict:
    """Inspect several URLs one by one. At most 10 per call. Earlier results are kept if quota runs out."""
    async with _client() as client:
        results, errors = await inspect_urls(client, site_url, urls, language_code)
    return {
        "meta": meta(
            property=site_url,
            start_date=None,
            end_date=None,
            search_type=None,
            data_state=None,
            row_count=len(results),
            truncated=False,
        ),
        "results": results,
        "errors": errors,
    }


@mcp.tool(annotations=READ_ONLY)
async def data_freshness(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    lookback_days: Annotated[
        int,
        Field(
            ge=1,
            le=FRESHNESS_MAX_LOOKBACK_DAYS,
            description="How many days back to scan, default 10, maximum 30.",
        ),
    ] = FRESHNESS_DEFAULT_LOOKBACK_DAYS,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
) -> dict:
    """Return the latest day with final data and the latest day with fresh data."""
    today = date.today()
    start = (today - timedelta(days=lookback_days - 1)).isoformat()
    end = today.isoformat()
    async with _client() as client:
        final_rows, fresh_rows = await asyncio.gather(
            client.search_analytics(
                site_url,
                start,
                end,
                dimensions=["date"],
                search_type=search_type,
                data_state="final",
                max_rows=lookback_days,
            ),
            client.search_analytics(
                site_url,
                start,
                end,
                dimensions=["date"],
                search_type=search_type,
                data_state="all",
                max_rows=lookback_days,
            ),
        )
    final_through = _latest_date(final_rows[0])
    fresh_through = _latest_date(fresh_rows[0])
    return {
        "meta": meta(
            property=site_url,
            start_date=start,
            end_date=end,
            search_type=search_type,
            data_state="all",
            row_count=2,
            truncated=final_rows[1] or fresh_rows[1],
            finalThrough=final_through,
            freshThrough=fresh_through,
        ),
        "finalThrough": final_through,
        "freshThrough": fresh_through,
    }


@mcp.tool(annotations=READ_ONLY)
async def performance_overview(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    start_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    end_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    top_countries: Annotated[
        int,
        Field(ge=1, le=50, description="How many countries to return, by clicks. " + COUNTRY_HELP),
    ] = 10,
) -> dict:
    """Totals, daily series, device split, and top countries.

    Country codes are ISO 3166-1 alpha-3 in lowercase, for example deu.
    If start_date or end_date is omitted, the range is the 28 days ending on the last day with final data.
    """
    async with _client() as client:
        used_start, used_end = await _resolved_range(
            client, site_url, start_date, end_date, search_type
        )
        day_count = (parse_date(used_end) - parse_date(used_start)).days + 1
        total, by_date, by_device, by_country = await asyncio.gather(
            client.search_analytics(
                site_url, used_start, used_end, search_type=search_type, data_state=data_state, max_rows=1
            ),
            client.search_analytics(
                site_url,
                used_start,
                used_end,
                dimensions=["date"],
                search_type=search_type,
                data_state=data_state,
                max_rows=min(MAX_ROWS, max(day_count, 1)),
            ),
            client.search_analytics(
                site_url,
                used_start,
                used_end,
                dimensions=["device"],
                search_type=search_type,
                data_state=data_state,
                max_rows=10,
            ),
            client.search_analytics(
                site_url,
                used_start,
                used_end,
                dimensions=["country"],
                search_type=search_type,
                data_state=data_state,
                max_rows=250,
            ),
        )
    body, _cut = compute_performance_overview(
        total[0], by_date[0], by_device[0], by_country[0], top_countries=top_countries
    )
    truncated = any(part[1] for part in (total, by_date, by_device, by_country))
    return {
        "meta": meta(
            property=site_url,
            start_date=used_start,
            end_date=used_end,
            search_type=search_type,
            data_state=data_state,
            row_count=len(body["byDate"]),
            truncated=truncated,
            topCountries=top_countries,
        ),
        **body,
    }


@mcp.tool(annotations=READ_ONLY)
async def compare_periods(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    dimension: Annotated[
        Literal["query", "page"],
        Field(description="Compare by query or by page."),
    ],
    start_date_a: Annotated[str, Field(description="Period A start, YYYY-MM-DD. Period A is the current period.")],
    end_date_a: Annotated[str, Field(description="Period A end, YYYY-MM-DD.")],
    start_date_b: Annotated[str, Field(description="Period B start, YYYY-MM-DD. Period B is the comparison period.")],
    end_date_b: Annotated[str, Field(description="Period B end, YYYY-MM-DD.")],
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ANALYSIS_ROWS,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS, description=LIMIT_HELP)] = DEFAULT_RESULT_LIMIT,
) -> dict:
    """Compare period A with period B for queries or pages.

    Deltas are A minus B. A negative deltaPosition means period A ranks better.
    Both date ranges are required.
    """
    period_a = require_range(start_date_a, end_date_a, label="period A")
    period_b = require_range(start_date_b, end_date_b, label="period B")
    async with _client() as client:
        fetched_a, fetched_b = await asyncio.gather(
            client.search_analytics(
                site_url, period_a[0], period_a[1],
                dimensions=[dimension], search_type=search_type, data_state=data_state, max_rows=max_rows,
            ),
            client.search_analytics(
                site_url, period_b[0], period_b[1],
                dimensions=[dimension], search_type=search_type, data_state=data_state, max_rows=max_rows,
            ),
        )
    rows, limited = compute_compare_periods(
        fetched_a[0], fetched_b[0], dimension=dimension, limit=limit
    )
    return {
        "meta": meta(
            property=site_url,
            start_date=period_a[0],
            end_date=period_a[1],
            search_type=search_type,
            data_state=data_state,
            row_count=len(rows),
            truncated=limited or fetched_a[1] or fetched_b[1],
            periodBStartDate=period_b[0],
            periodBEndDate=period_b[1],
        ),
        "rows": rows,
    }


@mcp.tool(annotations=READ_ONLY)
async def quick_wins(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    start_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    end_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    position_min: Annotated[float, Field(description="Lowest position to include, default 4.")] = 4,
    position_max: Annotated[float, Field(description="Highest position to include, default 20.")] = 20,
    min_impressions: Annotated[int, Field(ge=0, description="Minimum impressions, default 100.")] = 100,
    max_ctr_pct: Annotated[
        float | None,
        Field(description="If set, keep queries whose CTR percent is below this absolute cap."),
    ] = None,
    expected_ctr_ratio: Annotated[
        float,
        Field(
            gt=0,
            le=1,
            description="Keep queries below this fraction of the generic position CTR curve. Default 0.5. Ignored when max_ctr_pct is set.",
        ),
    ] = 0.5,
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ANALYSIS_ROWS,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS, description=LIMIT_HELP)] = DEFAULT_RESULT_LIMIT,
) -> dict:
    """Find queries in positions 4–20 with many impressions and a CTR below expectation.

    expectedCtrPct is an estimate. expectedCtrBasis is "Schätzung (generische Kurve)".
    If start_date or end_date is omitted, the range is the 28 days ending on the last day with final data.
    """
    async with _client() as client:
        used_start, used_end = await _resolved_range(
            client, site_url, start_date, end_date, search_type
        )
        fetched, fetch_truncated = await client.search_analytics(
            site_url,
            used_start,
            used_end,
            dimensions=["query"],
            search_type=search_type,
            data_state=data_state,
            max_rows=max_rows,
        )
    rows, limited = compute_quick_wins(
        fetched,
        position_min=position_min,
        position_max=position_max,
        min_impressions=min_impressions,
        max_ctr_pct=max_ctr_pct,
        expected_ctr_ratio=expected_ctr_ratio,
        limit=limit,
    )
    return {
        "meta": meta(
            property=site_url,
            start_date=used_start,
            end_date=used_end,
            search_type=search_type,
            data_state=data_state,
            row_count=len(rows),
            truncated=fetch_truncated or limited,
            expectedCtrBasis=EXPECTED_CTR_BASIS,
        ),
        "rows": rows,
    }


@mcp.tool(annotations=READ_ONLY)
async def cannibalization(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    start_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    end_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    min_page_impressions: Annotated[int, Field(ge=0, description="Impressions a URL needs to count, default 10.")] = 10,
    min_pages: Annotated[int, Field(ge=2, description="How many such URLs a query needs, default 2.")] = 2,
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ANALYSIS_ROWS,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS, description=LIMIT_HELP)] = DEFAULT_RESULT_LIMIT,
) -> dict:
    """Find queries where several URLs each earn relevant impressions.

    If start_date or end_date is omitted, the range is the 28 days ending on the last day with final data.
    """
    async with _client() as client:
        used_start, used_end = await _resolved_range(
            client, site_url, start_date, end_date, search_type
        )
        fetched, fetch_truncated = await client.search_analytics(
            site_url,
            used_start,
            used_end,
            dimensions=["query", "page"],
            search_type=search_type,
            data_state=data_state,
            max_rows=max_rows,
        )
    rows, limited = compute_cannibalization(
        fetched,
        min_page_impressions=min_page_impressions,
        min_pages=min_pages,
        limit=limit,
    )
    return {
        "meta": meta(
            property=site_url,
            start_date=used_start,
            end_date=used_end,
            search_type=search_type,
            data_state=data_state,
            row_count=len(rows),
            truncated=fetch_truncated or limited,
        ),
        "rows": rows,
    }


@mcp.tool(annotations=READ_ONLY)
async def content_decay(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    start_date_a: Annotated[str, Field(description="Recent period start, YYYY-MM-DD.")],
    end_date_a: Annotated[str, Field(description="Recent period end, YYYY-MM-DD.")],
    start_date_b: Annotated[str, Field(description="Baseline period start, YYYY-MM-DD.")],
    end_date_b: Annotated[str, Field(description="Baseline period end, YYYY-MM-DD.")],
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    min_baseline_clicks: Annotated[int, Field(ge=0, description="Minimum baseline clicks, default 20.")] = 20,
    min_drop_ratio: Annotated[
        float,
        Field(gt=0, le=1, description="Minimum relative click drop from baseline to recent, default 0.3."),
    ] = 0.3,
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ANALYSIS_ROWS,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS, description=LIMIT_HELP)] = DEFAULT_RESULT_LIMIT,
) -> dict:
    """Pages that lost a large share of clicks from the baseline period to the recent period. Both ranges are required."""
    recent = require_range(start_date_a, end_date_a, label="the recent period")
    baseline = require_range(start_date_b, end_date_b, label="the baseline period")
    async with _client() as client:
        fetched_recent, fetched_base = await asyncio.gather(
            client.search_analytics(
                site_url, recent[0], recent[1],
                dimensions=["page"], search_type=search_type, data_state=data_state, max_rows=max_rows,
            ),
            client.search_analytics(
                site_url, baseline[0], baseline[1],
                dimensions=["page"], search_type=search_type, data_state=data_state, max_rows=max_rows,
            ),
        )
    rows, limited = compute_content_decay(
        fetched_recent[0],
        fetched_base[0],
        min_baseline_clicks=min_baseline_clicks,
        min_drop_ratio=min_drop_ratio,
        limit=limit,
    )
    return {
        "meta": meta(
            property=site_url,
            start_date=recent[0],
            end_date=recent[1],
            search_type=search_type,
            data_state=data_state,
            row_count=len(rows),
            truncated=limited or fetched_recent[1] or fetched_base[1],
            periodBStartDate=baseline[0],
            periodBEndDate=baseline[1],
        ),
        "rows": rows,
    }


@mcp.tool(annotations=READ_ONLY)
async def new_and_lost_queries(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    start_date_a: Annotated[str, Field(description="Current period start, YYYY-MM-DD.")],
    end_date_a: Annotated[str, Field(description="Current period end, YYYY-MM-DD.")],
    start_date_b: Annotated[str, Field(description="Previous period start, YYYY-MM-DD.")],
    end_date_b: Annotated[str, Field(description="Previous period end, YYYY-MM-DD.")],
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    min_impressions: Annotated[int, Field(ge=0, description="Minimum impressions on the side that has the query, default 10.")] = 10,
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ANALYSIS_ROWS,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS, description=LIMIT_HELP)] = DEFAULT_RESULT_LIMIT,
) -> dict:
    """Queries that appear in the current period only, and queries that appear in the previous period only. Both ranges are required."""
    current = require_range(start_date_a, end_date_a, label="the current period")
    previous = require_range(start_date_b, end_date_b, label="the previous period")
    async with _client() as client:
        fetched_current, fetched_previous = await asyncio.gather(
            client.search_analytics(
                site_url, current[0], current[1],
                dimensions=["query"], search_type=search_type, data_state=data_state, max_rows=max_rows,
            ),
            client.search_analytics(
                site_url, previous[0], previous[1],
                dimensions=["query"], search_type=search_type, data_state=data_state, max_rows=max_rows,
            ),
        )
    fresh, lost, limited = compute_new_and_lost(
        fetched_current[0],
        fetched_previous[0],
        min_impressions=min_impressions,
        limit=limit,
    )
    return {
        "meta": meta(
            property=site_url,
            start_date=current[0],
            end_date=current[1],
            search_type=search_type,
            data_state=data_state,
            row_count=len(fresh) + len(lost),
            truncated=limited or fetched_current[1] or fetched_previous[1],
            periodBStartDate=previous[0],
            periodBEndDate=previous[1],
        ),
        "newQueries": fresh,
        "lostQueries": lost,
    }


@mcp.tool(annotations=READ_ONLY)
async def brand_split(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    brand_regex: Annotated[
        str,
        Field(description="Regular expression matched against queries, case-insensitive. No brand is built in."),
    ],
    start_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    end_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ANALYSIS_ROWS,
) -> dict:
    """Split query totals into brand and non-brand using the supplied regular expression.

    If start_date or end_date is omitted, the range is the 28 days ending on the last day with final data.
    """
    async with _client() as client:
        used_start, used_end = await _resolved_range(
            client, site_url, start_date, end_date, search_type
        )
        fetched, truncated = await client.search_analytics(
            site_url,
            used_start,
            used_end,
            dimensions=["query"],
            search_type=search_type,
            data_state=data_state,
            max_rows=max_rows,
        )
    split = compute_brand_split(fetched, brand_regex)
    return {
        "meta": meta(
            property=site_url,
            start_date=used_start,
            end_date=used_end,
            search_type=search_type,
            data_state=data_state,
            row_count=split["brand"]["rows"] + split["nonBrand"]["rows"],
            truncated=truncated,
        ),
        **split,
    }


@mcp.tool(annotations=READ_ONLY)
async def query_page_matrix(
    site_url: Annotated[str, Field(description=SITE_HELP)],
    page: Annotated[str | None, Field(description="Page URL. Set this or query, not both.")] = None,
    query: Annotated[str | None, Field(description="Query text. Set this or page, not both.")] = None,
    start_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    end_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS, description=MAX_ROWS_HELP)] = DEFAULT_ROWS,
) -> dict:
    """List every query for one page, or every page for one query.

    If start_date or end_date is omitted, the range is the 28 days ending on the last day with final data.
    """
    dimensions, filters = matrix_request(page, query)
    async with _client() as client:
        used_start, used_end = await _resolved_range(
            client, site_url, start_date, end_date, search_type
        )
        rows, truncated = await client.search_analytics(
            site_url,
            used_start,
            used_end,
            dimensions=dimensions,
            filters=filters,
            search_type=search_type,
            data_state=data_state,
            max_rows=max_rows,
        )
    return {
        "meta": meta(
            property=site_url,
            start_date=used_start,
            end_date=used_end,
            search_type=search_type,
            data_state=data_state,
            row_count=len(rows),
            truncated=truncated,
        ),
        "rows": [present_row(row) for row in rows],
    }


@mcp.tool(annotations=READ_ONLY)
async def portfolio_overview(
    start_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    end_date: Annotated[str | None, Field(description=DATE_HELP)] = None,
    site_urls: Annotated[
        list[str] | None,
        Field(description="Properties to include. Omit to use the account's properties, up to limit."),
    ] = None,
    search_type: Annotated[SearchType, Field(description=SEARCH_TYPE_HELP)] = "web",
    data_state: Annotated[DataState, Field(description=DATA_STATE_HELP)] = "final",
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=PORTFOLIO_MAX_LIMIT,
            description=f"Maximum properties, default {PORTFOLIO_DEFAULT_LIMIT}, hard cap {PORTFOLIO_MAX_LIMIT}.",
        ),
    ] = PORTFOLIO_DEFAULT_LIMIT,
) -> dict:
    """Totals for several properties, five at a time.

    A property that fails is listed in errors. The other properties are still returned.
    meta.completed and meta.total report progress. If start_date or end_date is omitted,
    the range is the 28 days ending on the last final day of the first property.
    """
    if not 1 <= limit <= PORTFOLIO_MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {PORTFOLIO_MAX_LIMIT}")
    async with _client() as client:
        if site_urls:
            available = list(site_urls)
        else:
            available = [site["siteUrl"] for site in await client.list_sites()]
        selected = available[:limit]
        if start_date and end_date:
            used_start, used_end = resolve_explicit_or_default(
                start_date, end_date, last_final=date.today()
            )
        elif selected:
            try:
                used_start, used_end = await _resolved_range(
                    client, selected[0], start_date, end_date, search_type
                )
            except GscApiError:
                used_start, used_end = _fallback_range()
        else:
            used_start, used_end = _fallback_range()

        async def one(site: str) -> dict:
            rows, _truncated = await client.search_analytics(
                site,
                used_start,
                used_end,
                search_type=search_type,
                data_state=data_state,
                max_rows=1,
            )
            if rows:
                metrics = present_metrics(
                    rows[0]["clicks"],
                    rows[0]["impressions"],
                    rows[0]["ctr"],
                    rows[0]["position"],
                )
            else:
                metrics = present_metrics(0, 0, 0, 0)
            metrics["property"] = site
            return metrics

        results = await gather_limited(selected, one, limit=PORTFOLIO_CONCURRENCY)

    rows = []
    errors = []
    for site, result in zip(selected, results):
        if isinstance(result, Exception):
            message = str(result) if isinstance(result, GscApiError) else "Query failed"
            errors.append({"property": site, "message": redact_text(message)})
        else:
            rows.append(result)
    return {
        "meta": meta(
            property=None,
            start_date=used_start,
            end_date=used_end,
            search_type=search_type,
            data_state=data_state,
            row_count=len(rows),
            truncated=len(available) > len(selected),
            completed=len(results),
            total=len(selected),
        ),
        "rows": rows,
        "errors": errors,
    }


@mcp.tool(annotations=LOCAL_READ_ONLY)
async def get_capabilities() -> dict:
    """List every tool on this server with its description."""
    listed = await mcp.list_tools(run_middleware=False)
    tools = [
        {"name": tool.name, "description": (tool.description or "").strip()}
        for tool in listed
    ]
    return {
        "meta": meta(
            property=None,
            start_date=None,
            end_date=None,
            search_type=None,
            data_state=None,
            row_count=len(tools),
            truncated=False,
        ),
        "tools": tools,
    }


def _latest_date(rows: list[dict]) -> str | None:
    dates = [
        row["keys"]["date"]
        for row in rows
        if isinstance((row.get("keys") or {}).get("date"), str)
    ]
    return max(dates) if dates else None
