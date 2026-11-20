"""Rounding and compact projections."""

from __future__ import annotations

from gsc_mcp.formatting import compact_inspection, compact_sitemap, meta, present_metrics, present_row


def test_metrics_are_rounded_and_ctr_is_a_percent():
    assert present_metrics(1.6, 10.2, 0.03251, 4.26) == {
        "clicks": 2,
        "impressions": 10,
        "ctrPct": 3.25,
        "position": 4.3,
    }
    assert present_metrics(0, 0, 0, None)["position"] is None
    presented = present_row(
        {
            "keys": {"query": "example", "country": "deu"},
            "clicks": 1,
            "impressions": 4,
            "ctr": 0.25,
            "position": 2.04,
        }
    )
    assert presented["country"] == "deu"
    assert presented["ctrPct"] == 25.0


def test_meta_always_contains_the_standard_keys():
    payload = meta(
        property="sc-domain:example.com",
        start_date="2026-10-20",
        end_date="2026-11-16",
        search_type="web",
        data_state="final",
        row_count=3,
        truncated=False,
        completed=3,
        total=3,
        unused=None,
    )
    assert payload["property"] == "sc-domain:example.com"
    assert payload["startDate"] == "2026-10-20"
    assert payload["rowCount"] == 3
    assert payload["truncated"] is False
    assert payload["completed"] == 3
    assert "unused" not in payload


def test_sitemap_and_inspection_are_compact():
    sitemap = compact_sitemap(
        {
            "path": "https://www.example.com/sitemap.xml",
            "isPending": False,
            "isSitemapsIndex": False,
            "warnings": "2",
            "errors": "1",
            "contents": [{"type": "web", "submitted": "10", "indexed": "8"}],
        }
    )
    assert sitemap["warnings"] == 2
    assert sitemap["contents"][0]["indexed"] == 8
    inspected = compact_inspection(
        {
            "inspectionResult": {
                "indexStatusResult": {
                    "verdict": "PASS",
                    "coverageState": "Submitted and indexed",
                    "googleCanonical": "https://www.example.com/a",
                    "userCanonical": "https://www.example.com/a",
                    "crawledAs": "MOBILE",
                },
                "mobileUsabilityResult": {"verdict": "PASS", "issues": []},
                "richResultsResult": {
                    "verdict": "PASS",
                    "detectedItems": [{"richResultType": "Product"}],
                },
            }
        }
    )
    assert inspected["indexStatus"]["verdict"] == "PASS"
    assert inspected["richResults"]["types"] == ["Product"]
