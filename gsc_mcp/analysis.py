"""Calculations on rows that have already been fetched. No network."""

from __future__ import annotations

import re

from gsc_mcp.formatting import present_metrics, present_row

# Generic organic CTR by rounded position, in percent. This is a rough
# industry-shaped curve used only as a threshold, not a Search Console measurement.
EXPECTED_CTR_BASIS = "Schätzung (generische Kurve)"
_EXPECTED_CTR_PCT = {
    1: 28.0,
    2: 15.0,
    3: 11.0,
    4: 8.0,
    5: 6.0,
    6: 4.5,
    7: 3.5,
    8: 3.0,
    9: 2.5,
    10: 2.0,
}


def expected_ctr_pct(position: float) -> float:
    """Expected CTR percent for a position, from the generic curve."""
    slot = int(round(position))
    if slot <= 1:
        return _EXPECTED_CTR_PCT[1]
    if slot in _EXPECTED_CTR_PCT:
        return _EXPECTED_CTR_PCT[slot]
    if slot >= 20:
        return 0.5
    return round(2.0 + (0.5 - 2.0) * (slot - 10) / 10, 2)


def sum_metrics(rows: list[dict]) -> dict:
    """Sum clicks and impressions. Position is weighted by impressions."""
    clicks = sum(float(row.get("clicks") or 0) for row in rows)
    impressions = sum(float(row.get("impressions") or 0) for row in rows)
    if impressions:
        position = (
            sum(
                float(row.get("position") or 0) * float(row.get("impressions") or 0)
                for row in rows
            )
            / impressions
        )
        ctr = clicks / impressions
    else:
        position = 0.0
        ctr = 0.0
    totals = present_metrics(clicks, impressions, ctr, position)
    totals["rows"] = len(rows)
    return totals


def performance_overview(
    total_rows: list[dict],
    by_date: list[dict],
    by_device: list[dict],
    by_country: list[dict],
    *,
    top_countries: int,
) -> tuple[dict, bool]:
    """Shape the four overview queries. Countries are cut to `top_countries`."""
    countries = sorted(by_country, key=lambda row: -float(row.get("clicks") or 0))
    truncated = len(countries) > top_countries
    totals = (
        present_metrics(
            total_rows[0].get("clicks"),
            total_rows[0].get("impressions"),
            total_rows[0].get("ctr"),
            total_rows[0].get("position"),
        )
        if total_rows
        else present_metrics(0, 0, 0, 0)
    )
    return {
        "totals": totals,
        "byDate": [present_row(row) for row in by_date],
        "byDevice": [present_row(row) for row in by_device],
        "byCountry": [present_row(row) for row in countries[:top_countries]],
    }, truncated


def compare_periods(
    rows_a: list[dict],
    rows_b: list[dict],
    *,
    dimension: str,
    limit: int,
) -> tuple[list[dict], bool]:
    """Outer-join period A and period B. Deltas are A minus B."""

    def index(rows: list[dict]) -> dict[str, dict]:
        return {str((row.get("keys") or {}).get(dimension, "")): row for row in rows}

    indexed_a = index(rows_a)
    indexed_b = index(rows_b)
    compared = []
    for key in set(indexed_a) | set(indexed_b):
        row_a = indexed_a.get(key)
        row_b = indexed_b.get(key)
        metrics_a = _metrics_or_empty(row_a)
        metrics_b = _metrics_or_empty(row_b)
        delta_position = None
        if row_a is not None and row_b is not None:
            delta_position = round(metrics_a["position"] - metrics_b["position"], 1)
        compared.append(
            {
                dimension: key,
                "a": metrics_a,
                "b": metrics_b,
                "deltaClicks": metrics_a["clicks"] - metrics_b["clicks"],
                "deltaImpressions": metrics_a["impressions"] - metrics_b["impressions"],
                "deltaCtrPp": round(metrics_a["ctrPct"] - metrics_b["ctrPct"], 2),
                "deltaPosition": delta_position,
            }
        )
    compared.sort(key=lambda item: (item["deltaClicks"], item[dimension]))
    return compared[:limit], len(compared) > limit


def quick_wins(
    rows: list[dict],
    *,
    position_min: float,
    position_max: float,
    min_impressions: int,
    max_ctr_pct: float | None,
    expected_ctr_ratio: float,
    limit: int,
) -> tuple[list[dict], bool]:
    """Queries in a position band whose CTR is below the chosen threshold."""
    found = []
    for row in rows:
        position = row.get("position")
        if position is None:
            continue
        position = float(position)
        impressions = float(row.get("impressions") or 0)
        ctr_pct = float(row.get("ctr") or 0) * 100
        if position < position_min or position > position_max:
            continue
        if impressions < min_impressions:
            continue
        expected = expected_ctr_pct(position)
        if max_ctr_pct is not None:
            below = ctr_pct < max_ctr_pct
        else:
            below = ctr_pct < expected * expected_ctr_ratio
        if not below:
            continue
        item = present_row(row)
        item["expectedCtrPct"] = round(expected, 2)
        item["expectedCtrBasis"] = EXPECTED_CTR_BASIS
        found.append(item)
    found.sort(key=lambda item: (-item["impressions"], item.get("query", "")))
    return found[:limit], len(found) > limit


def cannibalization(
    rows: list[dict],
    *,
    min_page_impressions: int,
    min_pages: int,
    limit: int,
) -> tuple[list[dict], bool]:
    """Queries where several URLs each have relevant impressions."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        query = str((row.get("keys") or {}).get("query", ""))
        grouped.setdefault(query, []).append(row)
    found = []
    for query, pages in grouped.items():
        relevant = [
            page
            for page in pages
            if float(page.get("impressions") or 0) >= min_page_impressions
        ]
        if len(relevant) < min_pages:
            continue
        total_impressions = sum(float(page.get("impressions") or 0) for page in relevant)
        total_clicks = sum(float(page.get("clicks") or 0) for page in relevant)
        page_rows = []
        for page in sorted(relevant, key=lambda item: -float(item.get("impressions") or 0)):
            presented = present_row(page)
            share = (
                0.0
                if total_impressions == 0
                else round(float(page.get("impressions") or 0) / total_impressions * 100, 2)
            )
            presented["impressionSharePct"] = share
            page_rows.append(presented)
        found.append(
            {
                "query": query,
                "clicks": int(round(total_clicks)),
                "impressions": int(round(total_impressions)),
                "pages": page_rows,
            }
        )
    found.sort(key=lambda item: (-item["impressions"], item["query"]))
    return found[:limit], len(found) > limit


def content_decay(
    recent: list[dict],
    baseline: list[dict],
    *,
    min_baseline_clicks: int,
    min_drop_ratio: float,
    limit: int,
) -> tuple[list[dict], bool]:
    """Pages whose clicks fell by at least `min_drop_ratio` from baseline to recent."""
    recent_by_page = {
        str((row.get("keys") or {}).get("page", "")): row for row in recent
    }
    found = []
    for row in baseline:
        page = str((row.get("keys") or {}).get("page", ""))
        before_clicks = float(row.get("clicks") or 0)
        if before_clicks < min_baseline_clicks:
            continue
        after = recent_by_page.get(page)
        after_clicks = float(after.get("clicks") or 0) if after else 0.0
        drop = (before_clicks - after_clicks) / before_clicks
        if drop < min_drop_ratio:
            continue
        found.append(
            {
                "page": page,
                "baseline": _metrics_or_empty(row),
                "recent": _metrics_or_empty(after),
                "clickDropRatio": round(drop, 4),
            }
        )
    found.sort(key=lambda item: (-item["clickDropRatio"], -item["baseline"]["clicks"], item["page"]))
    return found[:limit], len(found) > limit


def new_and_lost_queries(
    current: list[dict],
    previous: list[dict],
    *,
    min_impressions: int,
    limit: int,
) -> tuple[list[dict], list[dict], bool]:
    """Queries present in only one of the two periods."""
    current_by_query = _by_query(current)
    previous_by_query = _by_query(previous)
    fresh = _only_in(current_by_query, previous_by_query, min_impressions)
    lost = _only_in(previous_by_query, current_by_query, min_impressions)
    truncated = len(fresh) > limit or len(lost) > limit
    return fresh[:limit], lost[:limit], truncated


def brand_split(rows: list[dict], brand_regex: str) -> dict:
    """Split query rows into brand and non-brand totals."""
    pattern = brand_regex.strip()
    if not pattern:
        raise ValueError("brand_regex must not be empty")
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        raise ValueError("Invalid brand_regex") from None
    brand: list[dict] = []
    other: list[dict] = []
    for row in rows:
        query = str((row.get("keys") or {}).get("query", ""))
        if compiled.search(query):
            brand.append(row)
        else:
            other.append(row)
    return {"brand": sum_metrics(brand), "nonBrand": sum_metrics(other)}


def matrix_request(page: str | None, query: str | None) -> tuple[list[str], list[dict]]:
    """Exactly one of page or query. Returns the other dimension and an equals filter."""
    has_page = bool(page and page.strip())
    has_query = bool(query and query.strip())
    if has_page == has_query:
        raise ValueError("Provide exactly one of page or query")
    if has_page:
        return ["query"], [
            {"dimension": "page", "operator": "equals", "expression": page.strip()}
        ]
    return ["page"], [
        {"dimension": "query", "operator": "equals", "expression": query.strip()}
    ]


def _metrics_or_empty(row: dict | None) -> dict:
    if row is None:
        return present_metrics(0, 0, 0, None)
    return present_metrics(
        row.get("clicks"),
        row.get("impressions"),
        row.get("ctr"),
        row.get("position"),
    )


def _by_query(rows: list[dict]) -> dict[str, dict]:
    return {str((row.get("keys") or {}).get("query", "")): row for row in rows}


def _only_in(
    present: dict[str, dict],
    absent: dict[str, dict],
    min_impressions: int,
) -> list[dict]:
    rows = []
    for query, row in present.items():
        if query in absent:
            continue
        if float(row.get("impressions") or 0) < min_impressions:
            continue
        item = present_row(row)
        item["query"] = query
        rows.append(item)
    rows.sort(key=lambda item: (-item["impressions"], item["query"]))
    return rows
