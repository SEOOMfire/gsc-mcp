"""Pure analysis on fake Search Console rows."""

from __future__ import annotations

import pytest

from gsc_mcp.analysis import (
    EXPECTED_CTR_BASIS,
    brand_split,
    cannibalization,
    compare_periods,
    content_decay,
    expected_ctr_pct,
    matrix_request,
    new_and_lost_queries,
    performance_overview,
    quick_wins,
)


def row(keys, clicks, impressions, ctr, position):
    return {
        "keys": keys,
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "position": position,
    }


def test_expected_ctr_curve_is_labeled_as_generic():
    assert expected_ctr_pct(5) == 6.0
    assert expected_ctr_pct(11) == 1.85
    assert expected_ctr_pct(20) == 0.5
    assert EXPECTED_CTR_BASIS == "Schätzung (generische Kurve)"


def test_quick_wins_keeps_rows_below_the_curve_and_labels_the_estimate():
    rows = [
        row({"query": "alpha"}, 10, 200, 0.029, 5),
        row({"query": "beta"}, 10, 200, 0.031, 5),
        row({"query": "high"}, 10, 500, 0.01, 3),
        row({"query": "deep"}, 1, 200, 0.01, 21),
        row({"query": "thin"}, 1, 50, 0.01, 8),
    ]
    found, truncated = quick_wins(
        rows,
        position_min=4,
        position_max=20,
        min_impressions=100,
        max_ctr_pct=None,
        expected_ctr_ratio=0.5,
        limit=10,
    )
    assert [item["query"] for item in found] == ["alpha"]
    assert found[0]["expectedCtrPct"] == 6.0
    assert found[0]["expectedCtrBasis"] == EXPECTED_CTR_BASIS
    assert truncated is False


def test_quick_wins_absolute_ctr_cap_and_result_limit():
    rows = [
        row({"query": "one"}, 1, 300, 0.01, 6),
        row({"query": "two"}, 1, 200, 0.01, 6),
    ]
    found, truncated = quick_wins(
        rows,
        position_min=4,
        position_max=20,
        min_impressions=10,
        max_ctr_pct=2,
        expected_ctr_ratio=0.5,
        limit=1,
    )
    assert [item["query"] for item in found] == ["one"]
    assert found[0]["expectedCtrBasis"] == EXPECTED_CTR_BASIS
    assert truncated is True


def test_compare_periods_deltas_and_missing_side():
    current = [row({"query": "alpha"}, 10, 100, 0.1, 2), row({"query": "new"}, 4, 40, 0.1, 8)]
    previous = [row({"query": "alpha"}, 4, 80, 0.05, 5), row({"query": "gone"}, 7, 70, 0.1, 3)]
    compared, truncated = compare_periods(current, previous, dimension="query", limit=10)
    by_query = {item["query"]: item for item in compared}
    assert by_query["alpha"]["deltaClicks"] == 6
    assert by_query["alpha"]["deltaImpressions"] == 20
    assert by_query["alpha"]["deltaCtrPp"] == 5.0
    assert by_query["alpha"]["deltaPosition"] == -3.0
    assert by_query["gone"]["deltaPosition"] is None
    assert by_query["gone"]["a"]["clicks"] == 0
    assert compared[0]["query"] == "gone"
    assert truncated is False


def test_cannibalization_groups_pages_with_share():
    rows = [
        row({"query": "alpha", "page": "https://www.example.com/a"}, 5, 100, 0.05, 4),
        row({"query": "alpha", "page": "https://www.example.com/b"}, 3, 50, 0.06, 8),
        row({"query": "alpha", "page": "https://www.example.com/c"}, 1, 2, 0.5, 12),
        row({"query": "beta", "page": "https://www.example.com/d"}, 4, 80, 0.05, 3),
    ]
    found, truncated = cannibalization(rows, min_page_impressions=10, min_pages=2, limit=10)
    assert truncated is False
    assert [item["query"] for item in found] == ["alpha"]
    assert found[0]["impressions"] == 150
    assert found[0]["pages"][0]["impressionSharePct"] == 66.67


def test_content_decay_requires_a_real_drop():
    recent = [
        row({"page": "https://www.example.com/a"}, 40, 80, 0.5, 6),
        row({"page": "https://www.example.com/c"}, 45, 90, 0.5, 4),
    ]
    baseline = [
        row({"page": "https://www.example.com/a"}, 100, 200, 0.5, 4),
        row({"page": "https://www.example.com/b"}, 10, 20, 0.5, 4),
        row({"page": "https://www.example.com/c"}, 50, 100, 0.5, 4),
    ]
    found, truncated = content_decay(
        recent, baseline, min_baseline_clicks=20, min_drop_ratio=0.3, limit=10
    )
    assert truncated is False
    assert [item["page"] for item in found] == ["https://www.example.com/a"]
    assert found[0]["clickDropRatio"] == 0.6
    assert found[0]["recent"]["clicks"] == 40


def test_new_and_lost_queries():
    current = [row({"query": "kept"}, 5, 50, 0.1, 3), row({"query": "fresh"}, 2, 20, 0.1, 9)]
    previous = [row({"query": "kept"}, 4, 40, 0.1, 4), row({"query": "gone"}, 3, 30, 0.1, 6)]
    fresh, lost, truncated = new_and_lost_queries(
        current, previous, min_impressions=10, limit=10
    )
    assert [item["query"] for item in fresh] == ["fresh"]
    assert [item["query"] for item in lost] == ["gone"]
    assert truncated is False


def test_brand_split_and_invalid_regex():
    rows = [
        row({"query": "example shoes"}, 10, 100, 0.1, 2),
        row({"query": "other shoes"}, 5, 50, 0.1, 4),
    ]
    split = brand_split(rows, r"example")
    assert split["brand"]["clicks"] == 10
    assert split["nonBrand"]["clicks"] == 5
    assert split["brand"]["rows"] == 1
    with pytest.raises(ValueError, match="Invalid brand_regex"):
        brand_split(rows, "[")
    with pytest.raises(ValueError, match="empty"):
        brand_split(rows, "  ")


def test_matrix_request_requires_exactly_one_side():
    dimensions, filters = matrix_request("https://www.example.com/a", None)
    assert dimensions == ["query"]
    assert filters[0]["expression"] == "https://www.example.com/a"
    dimensions, filters = matrix_request(None, "example shoes")
    assert dimensions == ["page"]
    assert filters[0]["dimension"] == "query"
    with pytest.raises(ValueError):
        matrix_request(None, None)
    with pytest.raises(ValueError):
        matrix_request("https://www.example.com/a", "shoes")


def test_performance_overview_sorts_countries_and_marks_the_cut():
    body, truncated = performance_overview(
        [row({}, 15, 150, 0.1, 3.2)],
        [row({"date": "2026-11-01"}, 15, 150, 0.1, 3.2)],
        [row({"device": "MOBILE"}, 9, 90, 0.1, 3), row({"device": "DESKTOP"}, 6, 60, 0.1, 4)],
        [
            row({"country": "deu"}, 2, 20, 0.1, 5),
            row({"country": "usa"}, 10, 100, 0.1, 3),
            row({"country": "fra"}, 3, 30, 0.1, 4),
        ],
        top_countries=2,
    )
    assert body["totals"]["clicks"] == 15
    assert body["totals"]["ctrPct"] == 10.0
    assert body["totals"]["position"] == 3.2
    assert [item["country"] for item in body["byCountry"]] == ["usa", "fra"]
    assert truncated is True
    assert body["byDate"][0]["date"] == "2026-11-01"
