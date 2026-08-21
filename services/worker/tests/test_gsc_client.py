from datetime import date

import httpx
import pytest
from app.gsc.client import ROW_LIMIT, SearchConsoleClient, SearchConsoleError


@pytest.mark.asyncio
async def test_query_uses_read_only_bounded_daily_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sc-domain:example.com/searchAnalytics/query")
        assert b"sc-domain%3Aexample.com" in request.url.raw_path
        assert request.headers["Authorization"] == "Bearer access-token"
        body = __import__("json").loads(request.content)
        assert body == {
            "startDate": "2026-08-01",
            "endDate": "2026-08-01",
            "dimensions": ["query", "page", "country", "device"],
            "type": "web",
            "dataState": "final",
            "rowLimit": ROW_LIMIT,
            "startRow": ROW_LIMIT,
        }
        return httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "keys": ["private query", "https://example.com/a", "usa", "mobile"],
                        "clicks": 2,
                        "impressions": 20,
                        "ctr": 0.1,
                        "position": 7.5,
                    }
                ]
            },
        )

    client = SearchConsoleClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rows = await client.query_day(
        "sc-domain:example.com", "access-token", date(2026, 8, 1), ROW_LIMIT
    )
    assert rows[0].query == "private query"
    assert rows[0].position == 7.5


@pytest.mark.asyncio
async def test_provider_auth_failure_is_redacted() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="token details"))
    client = SearchConsoleClient(httpx.AsyncClient(transport=transport))
    with pytest.raises(SearchConsoleError, match="^authorization_required$"):
        await client.query_day("sc-domain:example.com", "secret", date(2026, 8, 1), 0)
