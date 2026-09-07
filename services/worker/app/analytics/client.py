"""Reading a day of GA4 landing-page metrics.

Search Console hands back rows and leaves the caller to discover the end of a
day by asking for one more page. The GA4 Data API reports `rowCount`, so
completion is knowable rather than inferred, and the sync stops without a final
empty request.

Two things are checked that a report client would usually take on trust. The
response's dimension and metric headers are compared against what was asked for,
in order, because every value in a row is positional: a provider that reorders
columns would otherwise write sessions into the key-events column and nothing
would notice. And a landing page is stored exactly as GA4 reported it -- path
plus query string, or the literal `(other)` bucket -- because normalising it
here would merge rows that the grain of the table keeps apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

ANALYTICS_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
DATA_API = "https://analyticsdata.googleapis.com/v1beta"

# GA4 permits far larger pages; this is small enough that one failed request
# costs little and large enough that an ordinary site is one or two requests.
ROW_LIMIT = 10_000

DIMENSIONS = ("landingPagePlusQueryString", "sessionDefaultChannelGroup", "deviceCategory")
METRICS = (
    "sessions",
    "engagedSessions",
    "totalUsers",
    "screenPageViews",
    "userEngagementDuration",
    "keyEvents",
)


class AnalyticsDataError(RuntimeError):
    """A bounded provider error safe to translate to a connector error code."""


@dataclass(frozen=True, slots=True)
class LandingPageRow:
    landing_page: str
    channel_group: str
    device: str
    sessions: float
    engaged_sessions: float
    users: float
    views: float
    engagement_duration_seconds: float
    key_events: float


@dataclass(frozen=True, slots=True)
class LandingPagePage:
    """One page of rows, and how many the report holds in total."""

    rows: tuple[LandingPageRow, ...]
    total_rows: int


class LandingPageSource(Protocol):
    """Where a day of rows comes from, credential included.

    The access token is deliberately not a parameter, for the reason the Search
    Console source gives: a loop holding a string it cannot renew dies partway
    through a long backfill and reports a working connector as needing consent.
    """

    async def query_day(self, property_ref: str, day: date, offset: int) -> LandingPagePage: ...


def landing_page_path(reported: str) -> str | None:
    """The path a reported landing page names, or nothing if it names none.

    GA4 reports a path with its query string, and sometimes reports `(other)`
    when a property exceeds its cardinality limit. `(other)` is a real bucket of
    sessions but not a page, so it is stored and left unresolved rather than
    turned into a URL that does not exist.
    """
    value = reported.strip()
    if not value.startswith("/"):
        return None
    path = urlsplit(value).path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return path


class AnalyticsDataClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def query_day(
        self, property_ref: str, access_token: str, day: date, offset: int
    ) -> LandingPagePage:
        if not property_ref or not access_token or offset < 0:
            raise ValueError("invalid_analytics_query")
        response = await self._client.post(
            f"{DATA_API}/{property_ref}:runReport",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "dateRanges": [{"startDate": day.isoformat(), "endDate": day.isoformat()}],
                "dimensions": [{"name": name} for name in DIMENSIONS],
                "metrics": [{"name": name} for name in METRICS],
                "limit": ROW_LIMIT,
                "offset": offset,
                "keepEmptyRows": False,
            },
        )
        if response.status_code in {401, 403}:
            raise AnalyticsDataError("authorization_required")
        if response.status_code == 429:
            raise AnalyticsDataError("provider_rate_limited")
        if response.status_code >= 500:
            raise AnalyticsDataError("provider_unavailable")
        if response.status_code >= 400:
            raise AnalyticsDataError("provider_request_rejected")
        return self._parse(response.json())

    @staticmethod
    def _parse(payload: Any) -> LandingPagePage:
        if not isinstance(payload, dict):
            raise AnalyticsDataError("provider_response_invalid")
        _assert_headers(payload.get("dimensionHeaders"), DIMENSIONS)
        _assert_headers(payload.get("metricHeaders"), METRICS)
        raw_rows = payload.get("rows", [])
        if not isinstance(raw_rows, list):
            raise AnalyticsDataError("provider_response_invalid")
        total = payload.get("rowCount", len(raw_rows))
        if not isinstance(total, int) or total < 0:
            raise AnalyticsDataError("provider_response_invalid")
        return LandingPagePage(tuple(_parse_row(row) for row in raw_rows), total)


def _assert_headers(headers: Any, expected: tuple[str, ...]) -> None:
    """Every value in a row is positional, so the columns have to be the ones asked for."""
    if headers is None:
        # A report with no rows may carry no headers. There is nothing
        # positional to get wrong in that case.
        return
    if not isinstance(headers, list) or len(headers) != len(expected):
        raise AnalyticsDataError("provider_response_invalid")
    for header, name in zip(headers, expected, strict=True):
        if not isinstance(header, dict) or header.get("name") != name:
            raise AnalyticsDataError("provider_response_columns_unexpected")


def _parse_row(value: Any) -> LandingPageRow:
    if not isinstance(value, dict):
        raise AnalyticsDataError("provider_response_invalid")
    dimensions = _values(value.get("dimensionValues"), len(DIMENSIONS))
    metrics = _values(value.get("metricValues"), len(METRICS))
    numbers = []
    for raw in metrics:
        try:
            number = float(raw)
        except (TypeError, ValueError) as error:
            raise AnalyticsDataError("provider_response_invalid") from error
        if number < 0:
            raise AnalyticsDataError("provider_response_invalid")
        numbers.append(number)
    return LandingPageRow(
        landing_page=dimensions[0][:2048],
        channel_group=dimensions[1][:120],
        device=dimensions[2][:60],
        sessions=numbers[0],
        engaged_sessions=numbers[1],
        users=numbers[2],
        views=numbers[3],
        engagement_duration_seconds=numbers[4],
        key_events=numbers[5],
    )


def _values(raw: Any, expected: int) -> list[str]:
    if not isinstance(raw, list) or len(raw) != expected:
        raise AnalyticsDataError("provider_response_invalid")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise AnalyticsDataError("provider_response_invalid")
        value = item.get("value")
        if not isinstance(value, str):
            raise AnalyticsDataError("provider_response_invalid")
        result.append(value)
    return result
