import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
MAX_RESPONSE_BYTES = 2_000_000


class PageSpeedError(RuntimeError):
    """A safe provider error code; response bodies are never retained."""


@dataclass(frozen=True, slots=True)
class PageSpeedResult:
    lighthouse_version: str
    performance_score: int
    lcp_ms: float | None
    inp_ms: float | None
    cls: float | None
    ttfb_ms: float | None


class PageSpeedProvider(Protocol):
    async def analyze(self, target_url: str, strategy: str) -> PageSpeedResult: ...


class PageSpeedClient:
    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(45.0))
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def analyze(self, target_url: str, strategy: str) -> PageSpeedResult:
        parsed = urlsplit(target_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid_pagespeed_target")
        if strategy not in {"mobile", "desktop"}:
            raise ValueError("invalid_pagespeed_strategy")
        params = {"url": target_url, "strategy": strategy, "category": "performance"}
        if self._api_key:
            params["key"] = self._api_key
        async with self._client.stream("GET", ENDPOINT, params=params) as response:
            if response.status_code == 429:
                raise PageSpeedError("provider_rate_limited")
            if response.status_code >= 500:
                raise PageSpeedError("provider_unavailable")
            if response.status_code >= 400:
                raise PageSpeedError("provider_request_rejected")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PageSpeedError("provider_response_too_large")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PageSpeedError("provider_response_invalid") from error
        return parse_result(payload)


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def parse_result(payload: object) -> PageSpeedResult:
    if not isinstance(payload, dict):
        raise PageSpeedError("provider_response_invalid")
    lighthouse = payload.get("lighthouseResult")
    if not isinstance(lighthouse, dict):
        raise PageSpeedError("provider_response_invalid")
    categories = lighthouse.get("categories")
    audits = lighthouse.get("audits")
    if not isinstance(categories, dict) or not isinstance(audits, dict):
        raise PageSpeedError("provider_response_invalid")
    performance = categories.get("performance")
    if not isinstance(performance, dict):
        raise PageSpeedError("provider_response_invalid")
    score = _number(performance.get("score"))
    version = lighthouse.get("lighthouseVersion")
    if score is None or score > 1 or not isinstance(version, str) or not version or len(version) > 80:
        raise PageSpeedError("provider_response_invalid")

    def audit_value(key: str) -> float | None:
        audit = audits.get(key)
        return _number(audit.get("numericValue")) if isinstance(audit, dict) else None

    return PageSpeedResult(
        lighthouse_version=version,
        performance_score=round(score * 100),
        lcp_ms=audit_value("largest-contentful-paint"),
        inp_ms=audit_value("interaction-to-next-paint"),
        cls=audit_value("cumulative-layout-shift"),
        ttfb_ms=audit_value("server-response-time"),
    )
