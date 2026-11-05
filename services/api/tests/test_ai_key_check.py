"""The stored-key probe: what each provider answer means, and what is sent."""

import httpx
import pytest

from app.services.ai_key_check import KeyCheckStatus, check_model

pytestmark = pytest.mark.asyncio


def client(status: int, seen: list[httpx.Request]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"id": "x"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (200, KeyCheckStatus.AVAILABLE),
        (404, KeyCheckStatus.MODEL_UNAVAILABLE),
        (401, KeyCheckStatus.KEY_REJECTED),
        (403, KeyCheckStatus.NOT_PERMITTED),
        (429, KeyCheckStatus.RATE_LIMITED),
        (500, KeyCheckStatus.UNREACHABLE),
    ],
)
async def test_each_answer_maps_to_one_status(code, expected):
    seen: list[httpx.Request] = []
    async with client(code, seen) as http:
        result = await check_model(http, provider="openai", api_key="sk-test", model="gpt-5")
    assert (result.model, result.status) == ("gpt-5", expected)


async def test_openai_is_asked_about_the_model_with_a_bearer_key():
    seen: list[httpx.Request] = []
    async with client(200, seen) as http:
        await check_model(http, provider="openai", api_key="sk-test", model="gpt-5")
    assert str(seen[0].url) == "https://api.openai.com/v1/models/gpt-5"
    assert seen[0].method == "GET"
    assert seen[0].headers["authorization"] == "Bearer sk-test"


async def test_anthropic_uses_its_own_header_and_version():
    seen: list[httpx.Request] = []
    async with client(200, seen) as http:
        await check_model(http, provider="anthropic", api_key="sk-ant-test", model="claude-opus-5")
    assert str(seen[0].url) == "https://api.anthropic.com/v1/models/claude-opus-5"
    assert seen[0].headers["x-api-key"] == "sk-ant-test"
    assert "authorization" not in seen[0].headers


async def test_a_network_failure_is_unreachable_not_an_exception():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        result = await check_model(http, provider="openai", api_key="sk-test", model="gpt-5")
    assert result.status is KeyCheckStatus.UNREACHABLE
