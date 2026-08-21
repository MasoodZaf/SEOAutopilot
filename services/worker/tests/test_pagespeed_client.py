import httpx
import pytest
from app.pagespeed.client import PageSpeedClient, PageSpeedError, parse_result


def payload() -> dict[str, object]:
    return {
        "lighthouseResult": {
            "lighthouseVersion": "12.8.2",
            "categories": {"performance": {"score": 0.73}},
            "audits": {
                "largest-contentful-paint": {"numericValue": 2510.5},
                "interaction-to-next-paint": {"numericValue": 182},
                "cumulative-layout-shift": {"numericValue": 0.084},
                "server-response-time": {"numericValue": 410},
            },
        }
    }


def test_parse_result_keeps_only_validated_summary_metrics() -> None:
    result = parse_result(payload())
    assert result.performance_score == 73
    assert result.lcp_ms == 2510.5
    assert result.inp_ms == 182
    assert result.cls == 0.084
    assert result.ttfb_ms == 410


def test_parse_result_rejects_invalid_score() -> None:
    bad = payload()
    bad["lighthouseResult"]["categories"]["performance"]["score"] = 2  # type: ignore[index]
    with pytest.raises(PageSpeedError, match="provider_response_invalid"):
        parse_result(bad)


@pytest.mark.asyncio
async def test_client_sends_bounded_contract_and_maps_rate_limit() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["url"] == "https://example.com/"
        assert request.url.params["strategy"] == "mobile"
        assert request.url.params["category"] == "performance"
        assert "key" not in request.url.params
        return httpx.Response(429)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PageSpeedClient(client=http)
        with pytest.raises(PageSpeedError, match="provider_rate_limited"):
            await client.analyze("https://example.com/", "mobile")
