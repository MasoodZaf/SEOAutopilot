import json

import httpx
import pytest

from app.services.google_oauth import GoogleOAuthError, GoogleOAuthHttpClient


@pytest.mark.asyncio
async def test_google_client_exchanges_code_and_lists_properties_without_query_tokens() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/token":
            assert b"client_secret=client-secret" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "scope": "https://www.googleapis.com/auth/webmasters.readonly",
                    "expires_in": 3600,
                },
            )
        assert request.url.path.endswith("/webmasters/v3/sites")
        assert request.headers["Authorization"] == "Bearer access"
        return httpx.Response(
            200,
            json={
                "siteEntry": [
                    {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GoogleOAuthHttpClient(
        http,
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost/callback",
    )
    grant = await client.exchange_code("one-time-code")
    properties = await client.list_properties(grant.access_token)
    assert grant.refresh_token == "refresh"
    assert properties[0].property_ref == "sc-domain:example.com"
    assert all("access" not in str(request.url) for request in requests)
    await http.aclose()


@pytest.mark.asyncio
async def test_google_error_body_is_not_exposed() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, content=json.dumps({"token": "leaked"}))
    )
    http = httpx.AsyncClient(transport=transport)
    client = GoogleOAuthHttpClient(
        http,
        client_id="client",
        client_secret="secret",
        redirect_uri="http://localhost/callback",
    )
    with pytest.raises(GoogleOAuthError, match="^oauth_code_exchange_failed$"):
        await client.exchange_code("bad-code")
    await http.aclose()
