"""Which days of search evidence a keyword refresh clusters.

The window was the last 28 calendar days. That is correct for a site with daily
traffic and useless for the sites the output would help most: Search Console
suppresses a day's query breakdown when the volume is small, so a site earning a
handful of impressions a week has evidence scattered across months. A fixed span
lands in a gap and reports "no search evidence" while dozens of real queries sit
in the table -- which is exactly what happened on the pilot site, with 52 stored
queries and a routine that could not see one of them.

Counting days that hold evidence makes both cases one rule.
"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.routines.runner import (
    KEYWORD_MAX_LOOKBACK_DAYS,
    KEYWORD_WINDOW_DAYS,
    _keyword_window,
    _run_keyword_refresh,
)

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
RUN = UUID("019d0000-0000-7000-8000-000000000013")
TODAY = date(2026, 9, 6)
KEY = b"k" * 32


class FakeConnection:
    """Serves the day list the window query would return, and records it."""

    def __init__(
        self,
        days: list[date],
        *,
        stored_queries: int = 52,
        analysis: dict[str, Any] | None = None,
    ) -> None:
        self.days = days
        self.stored_queries = stored_queries
        self.analysis = analysis if analysis is not None else {"queries_considered": 7}
        self.window_args: tuple[Any, ...] | None = None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        assert "FROM search_metric" in query
        self.window_args = args
        floor, ceiling, limit = args[2], args[3], args[4]
        # Mirror the SQL: bounded, newest first, capped.
        eligible = sorted(
            (day for day in self.days if floor <= day <= ceiling), reverse=True
        )
        return [{"metric_date": day} for day in eligible[:limit]]

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "FROM search_query" in query:
            return self.stored_queries
        return None


async def window(connection: FakeConnection, today: date = TODAY):
    return await _keyword_window(connection, TENANT, SITE, today)


@pytest.mark.asyncio
async def test_a_busy_site_still_gets_its_last_four_weeks() -> None:
    """The dense case must not change. It was never the broken one."""
    days = [date(2026, 9, 5) - timedelta(days=n) for n in range(60)]
    result = await window(FakeConnection(days))

    assert result is not None
    start, end, count = result
    assert end == date(2026, 9, 5)
    assert count == KEYWORD_WINDOW_DAYS
    assert (end - start).days + 1 == KEYWORD_WINDOW_DAYS


@pytest.mark.asyncio
async def test_evidence_scattered_across_months_is_still_found() -> None:
    """The pilot site's actual shape: 17 days spread over ten weeks.

    Under a fixed 28-day span every one of these is invisible, because the span
    ends today and the newest evidence is seven weeks old.
    """
    days = [
        date(2026, 5, 7), date(2026, 5, 19), date(2026, 5, 28),
        date(2026, 6, 2), date(2026, 6, 11), date(2026, 6, 19), date(2026, 6, 30),
        date(2026, 7, 4), date(2026, 7, 11), date(2026, 7, 18),
    ]
    result = await window(FakeConnection(days))

    assert result is not None
    start, end, count = result
    assert (start, end) == (date(2026, 5, 7), date(2026, 7, 18))
    assert count == 10
    # A span of 73 calendar days, holding 10 days of evidence. The old rule
    # would have looked at 2026-08-09..2026-09-05 and found nothing.
    assert (end - start).days + 1 == 73


@pytest.mark.asyncio
async def test_the_search_backwards_stops_at_a_year() -> None:
    """Demand drifts, and Search Console does not retain past this anyway."""
    days = [date(2024, 1, 5), date(2024, 2, 6), date(2026, 8, 1)]
    result = await window(FakeConnection(days))

    assert result is not None
    start, _end, count = result
    assert count == 1
    assert start == date(2026, 8, 1)
    assert start >= TODAY - timedelta(days=KEYWORD_MAX_LOOKBACK_DAYS)


@pytest.mark.asyncio
async def test_the_window_never_reaches_past_today() -> None:
    """A metric row dated ahead of the clock must not drag the window forward."""
    days = [date(2026, 9, 30), date(2026, 8, 2)]
    result = await window(FakeConnection(days))

    assert result is not None
    _start, end, _count = result
    assert end == date(2026, 8, 2)


@pytest.mark.asyncio
async def test_a_site_with_no_metrics_at_all_reports_nothing() -> None:
    assert await window(FakeConnection([])) is None


# --- the routine around it -------------------------------------------------


async def refresh(connection: FakeConnection, today: date = TODAY):
    return await _run_keyword_refresh(connection, TENANT, SITE, RUN, today, KEY)


@pytest.mark.asyncio
async def test_stale_evidence_is_clustered_and_its_age_reported(monkeypatch) -> None:
    """Old demand is still demand, but the reader gets to see how old.

    Refusing would leave the site exactly where it was: no clusters, no briefs,
    and 52 queries nobody ever looked at.
    """
    captured: dict[str, Any] = {}

    async def fake_analysis(_conn, _t, _s, window_start, window_end, _key, _run):
        captured["window"] = (window_start, window_end)
        return {"queries_considered": 7, "clusters_built": 3}

    monkeypatch.setattr("app.routines.runner.run_keyword_analysis", fake_analysis)
    connection = FakeConnection([date(2026, 7, 18), date(2026, 5, 7)])

    status, summary, skip = await refresh(connection)

    assert (status, skip) == ("completed", None)
    assert captured["window"] == (date(2026, 5, 7), date(2026, 7, 18))
    assert summary["days_with_evidence"] == 2
    assert summary["evidence_age_days"] == (TODAY - date(2026, 7, 18)).days
    assert summary["clusters_built"] == 3


@pytest.mark.asyncio
async def test_a_site_that_never_had_search_data_is_skipped(monkeypatch) -> None:
    async def fake_analysis(*_args):  # pragma: no cover - must not run
        raise AssertionError("clustering attempted with no stored queries")

    monkeypatch.setattr("app.routines.runner.run_keyword_analysis", fake_analysis)
    connection = FakeConnection([date(2026, 7, 18)], stored_queries=0)

    status, summary, skip = await refresh(connection)

    assert (status, skip) == ("skipped", "no_search_query_evidence")
    assert summary == {}


@pytest.mark.asyncio
async def test_stored_queries_with_no_metric_days_is_still_a_skip(monkeypatch) -> None:
    """The two tables can disagree; the window is what clustering needs."""

    async def fake_analysis(*_args):  # pragma: no cover - must not run
        raise AssertionError("clustering attempted with no window")

    monkeypatch.setattr("app.routines.runner.run_keyword_analysis", fake_analysis)

    status, _summary, skip = await refresh(FakeConnection([]))
    assert (status, skip) == ("skipped", "no_search_evidence_in_window")


@pytest.mark.asyncio
async def test_a_window_that_yields_no_clusterable_queries_is_reported(monkeypatch) -> None:
    async def fake_analysis(_conn, _t, _s, _start, _end, _key, _run):
        return {"queries_considered": 0, "clusters_built": 0}

    monkeypatch.setattr("app.routines.runner.run_keyword_analysis", fake_analysis)

    status, summary, skip = await refresh(FakeConnection([date(2026, 7, 18)]))

    assert (status, skip) == ("skipped", "no_search_evidence_in_window")
    # Even a skip carries the window it looked at, so the reason is checkable.
    assert summary["days_with_evidence"] == 1
