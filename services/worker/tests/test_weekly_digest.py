"""What the weekly digest says, and what it refuses to say.

The digest is what a person actually reads, so the honesty rules matter more
here than anywhere: an absence of evidence is reported as an absence rather than
as a zero, and Search Console and GA4 are never combined. Sessions and clicks
count different things over different windows; putting them in one figure
invites the reader to subtract one from the other and call the remainder a loss.

There was no coverage of this at all before GA4 was added to it.
"""

from datetime import date
from typing import Any
from uuid import UUID

import pytest
from app.routines.reports import REPORT_SCHEMA_VERSION, build_weekly_digest

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
START, END = date(2026, 9, 1), date(2026, 9, 7)

SITE_ROW = {
    "name": "TheCalcHive",
    "canonical_origin": "https://thecalchive.com",
    "mode": "recommend",
    "status": "active",
}


class FakeConnection:
    """Answers by matching the table a statement reads.

    Crude on purpose: the digest's shape is what is under test, not SQL.
    """

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def _key(self, query: str) -> str:
        self.calls.append(query)
        for needle, key in (
            ("FROM site", "site"),
            ("FROM crawl_job", "crawl"),
            ("FROM page", "pages"),
            ("FROM search_metric", "search"),
            ("FROM analytics_metric", "analytics"),
            ("GROUP BY landing_page", "landing"),
            ("FROM opportunity", "opportunity"),
            ("performance_observation", "performance"),
        ):
            if needle in query:
                return key
        return "unknown"

    async def fetchrow(self, query: str, *args: Any) -> Any:
        key = "landing" if "GROUP BY landing_page" in query else self._key(query)
        if key == "analytics":
            # The window is the third and fourth argument; the prior window is
            # the second call, so answers may differ per range.
            windows = self.answers.get("analytics", [])
            index = min(len([c for c in self.calls if "analytics_metric" in c]) - 1, len(windows) - 1)
            return windows[index] if windows else None
        return self.answers.get(key)

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        key = "landing" if "GROUP BY landing_page" in query else self._key(query)
        return self.answers.get(f"{key}_rows", [])

    async def fetchval(self, query: str, *args: Any) -> Any:
        return self.answers.get(f"{self._key(query)}_value", 0)


def connection(**overrides: Any) -> FakeConnection:
    answers: dict[str, Any] = {
        "site": SITE_ROW,
        "crawl": None,
        "pages_value": 32,
        "search": {"clicks": 0, "impressions": 0, "position": 0},
        "opportunity_rows": [],
        "opportunity_value": 0,
        "performance": None,
        "analytics": [],
        "landing_rows": [],
    }
    answers.update(overrides)
    return FakeConnection(**answers)


async def digest(conn: FakeConnection) -> dict[str, Any]:
    return await build_weekly_digest(conn, TENANT, SITE, START, END)


@pytest.mark.asyncio
async def test_no_analytics_evidence_is_an_absence_not_a_row_of_zeros() -> None:
    """Zeros would read as "nobody engaged", which is a claim about the site.

    The truthful statement is that nothing was measured.
    """
    payload = await digest(connection())

    assert payload["engagement"] == {
        "available": False,
        "reason": "no_analytics_evidence_in_window",
    }
    assert payload["schema_version"] == REPORT_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_engagement_reports_deltas_against_the_prior_window() -> None:
    payload = await digest(
        connection(
            analytics=[
                {
                    "sessions": 200,
                    "engaged_sessions": 120,
                    "key_events": 8,
                    "engagement_seconds": 6000,
                },
                {
                    "sessions": 100,
                    "engaged_sessions": 40,
                    "key_events": 2,
                    "engagement_seconds": 2000,
                },
            ],
            landing_rows=[
                {
                    "landing_page": "/emi-calculator",
                    "sessions": 150,
                    "engaged_sessions": 100,
                    "key_events": 6,
                }
            ],
        )
    )

    engagement = payload["engagement"]
    assert engagement["available"] is True
    assert engagement["sessions"]["current"] == 200
    assert engagement["sessions"]["previous"] == 100
    assert engagement["sessions"]["change_percent"] == 100.0
    # 120/200 against 40/100.
    assert engagement["engagement_rate"]["current"] == 0.6
    assert engagement["engagement_rate"]["previous"] == 0.4
    assert engagement["key_events"]["current"] == 8
    assert engagement["top_landing_pages"][0]["landing_page"] == "/emi-calculator"
    # Association over a window, never attribution to a change.
    assert engagement["interpretation"] == "period_over_period_association"


@pytest.mark.asyncio
async def test_a_first_window_with_no_prior_data_does_not_divide_by_zero() -> None:
    payload = await digest(
        connection(
            analytics=[
                {"sessions": 50, "engaged_sessions": 25, "key_events": 1,
                 "engagement_seconds": 900},
                {"sessions": 0, "engaged_sessions": 0, "key_events": 0,
                 "engagement_seconds": 0},
            ]
        )
    )

    engagement = payload["engagement"]
    assert engagement["sessions"]["change_percent"] is None
    assert engagement["engagement_rate"]["previous"] == 0.0
    assert engagement["engagement_rate"]["current"] == 0.5


@pytest.mark.asyncio
async def test_search_and_engagement_stay_separate_sections() -> None:
    """Clicks and sessions are different measurements of different things.

    One combined number would be read as a funnel, and the drop between them as
    a loss, when the two counters simply do not agree by construction.
    """
    payload = await digest(
        connection(
            search={"clicks": 83, "impressions": 900, "position": 75},
            analytics=[
                {"sessions": 40, "engaged_sessions": 20, "key_events": 0,
                 "engagement_seconds": 700},
                {"sessions": 30, "engaged_sessions": 15, "key_events": 0,
                 "engagement_seconds": 500},
            ],
        )
    )

    assert payload["search"]["available"] is True
    assert payload["engagement"]["available"] is True
    assert set(payload["search"]) & set(payload["engagement"]) == {
        "available",
        "interpretation",
    }
    combined = str(payload)
    assert "conversion_rate" not in combined
    assert "click_to_session" not in combined


@pytest.mark.asyncio
async def test_a_site_that_is_gone_is_an_error_not_an_empty_report() -> None:
    with pytest.raises(ValueError, match="site_not_found"):
        await digest(connection(site=None))
