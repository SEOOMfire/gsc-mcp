"""Date parsing and the default 28-day window."""

from __future__ import annotations

import re
from datetime import date, timedelta

from gsc_mcp.limits import DEFAULT_WINDOW_DAYS, FINAL_LAG_FALLBACK_DAYS

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value: str) -> date:
    """Parse a YYYY-MM-DD date."""
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        raise ValueError("Dates must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Dates must be YYYY-MM-DD") from None


def trailing_window(end: date, *, days: int = DEFAULT_WINDOW_DAYS) -> tuple[date, date]:
    """Return an inclusive window of `days` ending on `end`."""
    if days < 1:
        raise ValueError("days must be at least 1")
    return end - timedelta(days=days - 1), end


def pick_last_final_day(rows: list[dict], *, today: date) -> date:
    """Latest date dimension in final rows, or today minus the usual GSC lag."""
    found: list[date] = []
    for row in rows:
        raw = (row.get("keys") or {}).get("date")
        if not isinstance(raw, str):
            continue
        try:
            found.append(date.fromisoformat(raw))
        except ValueError:
            continue
    if not found:
        return today - timedelta(days=FINAL_LAG_FALLBACK_DAYS)
    return max(found)


def resolve_explicit_or_default(
    start: str | None,
    end: str | None,
    *,
    last_final: date,
) -> tuple[str, str]:
    """Use both explicit dates, or the 28 days ending on the last final day.

    If either date is missing, both are replaced by that default window.
    """
    if start and end:
        start_day = parse_date(start)
        end_day = parse_date(end)
        if start_day > end_day:
            raise ValueError("start_date must be on or before end_date")
        return start_day.isoformat(), end_day.isoformat()
    window_start, window_end = trailing_window(last_final)
    return window_start.isoformat(), window_end.isoformat()


def require_range(start: str | None, end: str | None, *, label: str) -> tuple[str, str]:
    """Require a complete inclusive range."""
    if not start or not end:
        raise ValueError(f"{label} requires start and end dates (YYYY-MM-DD)")
    return resolve_explicit_or_default(start, end, last_final=date.today())
