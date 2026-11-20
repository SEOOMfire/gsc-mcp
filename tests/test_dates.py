"""Default date window."""

from __future__ import annotations

from datetime import date

import pytest

from gsc_mcp.dates import (
    pick_last_final_day,
    require_range,
    resolve_explicit_or_default,
    trailing_window,
)


def test_explicit_dates_are_kept():
    start, end = resolve_explicit_or_default(
        "2026-01-01", "2026-01-31", last_final=date(2026, 11, 16)
    )
    assert (start, end) == ("2026-01-01", "2026-01-31")


def test_missing_either_date_uses_28_days_through_the_last_final_day():
    last = date(2026, 11, 16)
    assert trailing_window(last) == (date(2026, 10, 20), last)
    for start, end in ((None, None), ("2020-01-01", None), (None, "2020-01-31")):
        resolved = resolve_explicit_or_default(start, end, last_final=last)
        assert resolved == ("2026-10-20", "2026-11-16")


def test_last_final_day_falls_back_when_the_probe_is_empty():
    today = date(2026, 11, 20)
    assert pick_last_final_day([], today=today) == date(2026, 11, 17)
    rows = [
        {"keys": {"date": "2026-11-14"}},
        {"keys": {"date": "2026-11-16"}},
        {"keys": {}},
    ]
    assert pick_last_final_day(rows, today=today) == date(2026, 11, 16)


def test_reversed_range_is_rejected():
    with pytest.raises(ValueError):
        resolve_explicit_or_default("2026-02-02", "2026-02-01", last_final=date(2026, 2, 2))
    with pytest.raises(ValueError):
        require_range(None, "2026-02-01", label="period A")
    with pytest.raises(ValueError):
        resolve_explicit_or_default("2026-13-01", "2026-13-02", last_final=date(2026, 1, 1))
