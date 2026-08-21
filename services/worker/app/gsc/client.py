from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from urllib.parse import quote

import httpx

READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SEARCH_ANALYTICS_ENDPOINT = (
    "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"
)
ROW_LIMIT = 25_000


class SearchConsoleError(RuntimeError):
    """A bounded provider error safe to translate to a connector error code."""


@dataclass(frozen=True, slots=True)
class SearchAnalyticsRow:
    query: str
    page_url: str
    country: str
    device: str
    clicks: float
    impressions: float
    ctr: float
    position: float


class SearchConsoleProvider(Protocol):
    async def query_day(
        self, property_ref: str, access_token: str, day: date, start_row: int
    ) -> list[SearchAnalyticsRow]: ...


class SearchConsoleClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def query_day(
        self, property_ref: str, access_token: str, day: date, start_row: int
    ) -> list[SearchAnalyticsRow]:
        if not property_ref or not access_token or start_row < 0:
            raise ValueError("invalid_search_console_query")
        response = await self._client.post(
            SEARCH_ANALYTICS_ENDPOINT.format(site_url=quote(property_ref, safe="")),
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "startDate": day.isoformat(),
                "endDate": day.isoformat(),
                "dimensions": ["query", "page", "country", "device"],
                "type": "web",
                "dataState": "final",
                "rowLimit": ROW_LIMIT,
                "startRow": start_row,
            },
        )
        if response.status_code in {401, 403}:
            raise SearchConsoleError("authorization_required")
        if response.status_code == 429:
            raise SearchConsoleError("provider_rate_limited")
        if response.status_code >= 500:
            raise SearchConsoleError("provider_unavailable")
        if response.status_code >= 400:
            raise SearchConsoleError("provider_request_rejected")
        payload = response.json()
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise SearchConsoleError("provider_response_invalid")
        return [self._parse_row(row) for row in rows]

    @staticmethod
    def _parse_row(value: Any) -> SearchAnalyticsRow:
        if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
            raise SearchConsoleError("provider_response_invalid")
        keys = value["keys"]
        if len(keys) != 4 or not all(isinstance(item, str) for item in keys):
            raise SearchConsoleError("provider_response_invalid")
        try:
            clicks = float(value["clicks"])
            impressions = float(value["impressions"])
            ctr = float(value["ctr"])
            position = float(value["position"])
        except (KeyError, TypeError, ValueError) as error:
            raise SearchConsoleError("provider_response_invalid") from error
        if clicks < 0 or impressions < 0 or not 0 <= ctr <= 1 or position < 0:
            raise SearchConsoleError("provider_response_invalid")
        return SearchAnalyticsRow(
            query=keys[0],
            page_url=keys[1],
            country=keys[2],
            device=keys[3],
            clicks=clicks,
            impressions=impressions,
            ctr=ctr,
            position=position,
        )

