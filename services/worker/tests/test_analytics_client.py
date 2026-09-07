"""What the GA4 report client refuses to believe.

Every value in a returned row is positional, so the one failure worth guarding
is a response whose columns are not the ones that were asked for: sessions
written into the key-events column would be wrong in a way nothing downstream
could detect. The rest is the ordinary bounded translation of provider status
codes into codes a connector can act on.
"""

from datetime import date

import httpx
import pytest
from app.analytics.client import (
    DIMENSIONS,
    METRICS,
    AnalyticsDataClient,
    AnalyticsDataError,
    landing_page_path,
)

DAY = date(2026, 9, 5)


def response(rows, *, row_count=None, headers=True) -> dict:
    body: dict = {"rows": rows, "rowCount": row_count if row_count is not None else len(rows)}
    if headers:
        body["dimensionHeaders"] = [{"name": name} for name in DIMENSIONS]
        body["metricHeaders"] = [{"name": name} for name in METRICS]
    return body


def row(landing="/emi-calculator", metrics=("4", "3", "4", "5", "120", "1")) -> dict:
    return {
        "dimensionValues": [{"value": landing}, {"value": "Organic Search"}, {"value": "mobile"}],
        "metricValues": [{"value": value} for value in metrics],
    }


def client(handler) -> AnalyticsDataClient:
    return AnalyticsDataClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_a_report_becomes_rows_with_its_total() -> None:
    page = await client(
        lambda request: httpx.Response(200, json=response([row()], row_count=41))
    ).query_day("properties/1", "token", DAY, 0)

    assert page.total_rows == 41
    assert len(page.rows) == 1
    only = page.rows[0]
    assert only.landing_page == "/emi-calculator"
    assert only.channel_group == "Organic Search"
    assert only.device == "mobile"
    assert (only.sessions, only.engaged_sessions, only.key_events) == (4.0, 3.0, 1.0)
    assert only.engagement_duration_seconds == 120.0


@pytest.mark.asyncio
async def test_a_response_whose_columns_moved_is_refused() -> None:
    """The one mistake nothing downstream could notice.

    Values arrive positionally. A provider that reordered its columns would put
    engaged sessions where sessions belong, and every number after it would be
    wrong while looking entirely plausible.
    """
    body = response([row()])
    body["metricHeaders"] = [{"name": name} for name in reversed(METRICS)]
    with pytest.raises(AnalyticsDataError, match="columns_unexpected"):
        await client(lambda request: httpx.Response(200, json=body)).query_day(
            "properties/1", "token", DAY, 0
        )


@pytest.mark.asyncio
async def test_an_empty_report_is_allowed_to_carry_no_headers() -> None:
    page = await client(
        lambda request: httpx.Response(200, json=response([], headers=False))
    ).query_day("properties/1", "token", DAY, 0)
    assert page.rows == ()
    assert page.total_rows == 0


@pytest.mark.asyncio
async def test_provider_failures_become_codes_a_connector_can_act_on() -> None:
    cases = {
        401: "authorization_required",
        403: "authorization_required",
        429: "provider_rate_limited",
        500: "provider_unavailable",
        503: "provider_unavailable",
        400: "provider_request_rejected",
    }
    for code, expected in cases.items():
        with pytest.raises(AnalyticsDataError, match=expected):
            await client(lambda request, code=code: httpx.Response(code, json={})).query_day(
                "properties/1", "token", DAY, 0
            )


@pytest.mark.asyncio
async def test_a_malformed_row_is_refused_rather_than_partially_read() -> None:
    for broken in (
        {"dimensionValues": [{"value": "/a"}], "metricValues": [{"value": "1"}]},
        {"dimensionValues": [{"value": "/a"}, {"value": "x"}, {"value": "y"}],
         "metricValues": [{"value": "-1"}] * 6},
        {"dimensionValues": [{"value": "/a"}, {"value": "x"}, {"value": "y"}],
         "metricValues": [{"value": "not-a-number"}] * 6},
    ):
        body = response([broken])
        with pytest.raises(AnalyticsDataError):
            await client(
                lambda request, payload=body: httpx.Response(200, json=payload)
            ).query_day("properties/1", "token", DAY, 0)


def test_a_landing_page_resolves_to_a_path_or_to_nothing() -> None:
    """GA4's `(other)` bucket is real sessions but not a page.

    Turning it into a URL would invent one; leaving it unresolved keeps the
    sessions and admits they cannot be attributed.
    """
    assert landing_page_path("/emi-calculator") == "/emi-calculator"
    assert landing_page_path("/emi-calculator/") == "/emi-calculator"
    assert landing_page_path("/emi-calculator?utm_source=x") == "/emi-calculator"
    assert landing_page_path("/") == "/"
    assert landing_page_path("(other)") is None
    assert landing_page_path("") is None


@pytest.mark.asyncio
async def test_the_request_asks_for_one_day_and_the_offset_it_was_given() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json=response([]))

    await client(handler).query_day("properties/123", "token", DAY, 10_000)

    assert seen["dateRanges"] == [{"startDate": "2026-09-05", "endDate": "2026-09-05"}]
    assert seen["offset"] == 10_000
    assert [item["name"] for item in seen["dimensions"]] == list(DIMENSIONS)
    assert [item["name"] for item in seen["metrics"]] == list(METRICS)


@pytest.mark.asyncio
async def test_an_incomplete_query_never_reaches_the_provider() -> None:
    for property_ref, token, offset in (("", "t", 0), ("properties/1", "", 0), ("properties/1", "t", -1)):
        with pytest.raises(ValueError, match="invalid_analytics_query"):
            await client(
                lambda request: httpx.Response(500, json={})
            ).query_day(property_ref, token, DAY, offset)
