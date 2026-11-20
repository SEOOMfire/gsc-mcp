"""Compact JSON shapes for Search Console responses."""

from __future__ import annotations

from typing import Any


def present_metrics(clicks: Any, impressions: Any, ctr: Any, position: Any) -> dict:
    """Round API metrics. `ctr` is a 0–1 ratio. `position` None stays null."""
    rounded_position = None if position is None else round(float(position), 1)
    return {
        "clicks": int(round(float(clicks or 0))),
        "impressions": int(round(float(impressions or 0))),
        "ctrPct": round(float(ctr or 0) * 100, 2),
        "position": rounded_position,
    }


def present_row(row: dict) -> dict:
    """Flatten dimension keys and rounded metrics into one object."""
    presented = dict(row.get("keys") or {})
    presented.update(
        present_metrics(
            row.get("clicks"),
            row.get("impressions"),
            row.get("ctr"),
            row.get("position"),
        )
    )
    return presented


def meta(
    *,
    property: str | None,
    start_date: str | None,
    end_date: str | None,
    search_type: str | None,
    data_state: str | None,
    row_count: int,
    truncated: bool,
    **extra: Any,
) -> dict:
    """Standard envelope. Extra keys are added when their value is not None."""
    payload: dict[str, Any] = {
        "property": property,
        "startDate": start_date,
        "endDate": end_date,
        "searchType": search_type,
        "dataState": data_state,
        "rowCount": row_count,
        "truncated": truncated,
    }
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload


def _as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def compact_sitemap(item: dict) -> dict:
    """Keep sitemap status, errors, warnings, and submitted/indexed counts."""
    contents = []
    for entry in item.get("contents") or []:
        if not isinstance(entry, dict):
            continue
        contents.append(
            {
                "type": entry.get("type"),
                "submitted": _as_int(entry.get("submitted")),
                "indexed": _as_int(entry.get("indexed")),
            }
        )
    return {
        "path": item.get("path"),
        "lastSubmitted": item.get("lastSubmitted"),
        "isPending": bool(item.get("isPending")),
        "isSitemapsIndex": bool(item.get("isSitemapsIndex")),
        "type": item.get("type"),
        "lastDownloaded": item.get("lastDownloaded"),
        "warnings": _as_int(item.get("warnings")),
        "errors": _as_int(item.get("errors")),
        "contents": contents,
    }


def compact_inspection(payload: dict) -> dict:
    """Project a URL Inspection response down to the fields an analyst needs."""
    result = payload.get("inspectionResult") or {}
    index = result.get("indexStatusResult") or {}
    mobile = result.get("mobileUsabilityResult") or {}
    rich = result.get("richResultsResult") or {}
    issues = []
    for issue in (mobile.get("issues") or [])[:10]:
        if not isinstance(issue, dict):
            continue
        issues.append(
            {
                "issueType": issue.get("issueType"),
                "severity": issue.get("severity"),
                "message": issue.get("message"),
            }
        )
    types = []
    for item in rich.get("detectedItems") or []:
        if isinstance(item, dict) and item.get("richResultType"):
            types.append(item["richResultType"])
        if len(types) >= 10:
            break
    return {
        "inspectionResultLink": result.get("inspectionResultLink"),
        "indexStatus": {
            "verdict": index.get("verdict"),
            "coverageState": index.get("coverageState"),
            "indexingState": index.get("indexingState"),
            "robotsTxtState": index.get("robotsTxtState"),
            "pageFetchState": index.get("pageFetchState"),
            "crawledAs": index.get("crawledAs"),
            "lastCrawlTime": index.get("lastCrawlTime"),
            "googleCanonical": index.get("googleCanonical"),
            "userCanonical": index.get("userCanonical"),
            "referringUrls": list(index.get("referringUrls") or [])[:5],
            "sitemap": list(index.get("sitemap") or [])[:5],
        },
        "mobileUsability": {
            "verdict": mobile.get("verdict"),
            "issues": issues,
        },
        "richResults": {
            "verdict": rich.get("verdict"),
            "types": types,
        },
    }
