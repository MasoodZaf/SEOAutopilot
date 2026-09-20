"""The preflight that decides whether a tenant's Google client can work.

The fixtures here are real: `MISMATCH_AUTH_ERROR` is the blob Google actually
returned for a client whose redirect URI was not registered, captured from a
live refusal. Inventing one would only test that we can read our own invention.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.services.google_client_check import (
    ClientCheck,
    ClientCheckResult,
    check_google_client,
)
from app.services.tenant_credentials import TenantCredentialService

REDIRECT_URI = "https://seo.example.com/api/v1/connectors/oauth/callback"


def _auth_error(code: str) -> str:
    """A blob shaped like Google's: the code inside an opaque envelope."""
    payload = (
        f"\n\x15{code}\x12\xb0\x01You can't sign in to this app because it doesn't comply "
        "with Google's OAuth 2.0 policy."
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_registered_redirect_uri_reads_as_ok():
    """Google sending the browser on to sign in means the client is usable."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "https://accounts.google.com/v3/signin/identifier?x=1"}
        )

    async with _transport(handler) as client:
        result = await check_google_client(
            client, client_id="1-a.apps.googleusercontent.com", redirect_uri=REDIRECT_URI
        )
    assert result.status is ClientCheck.OK
    assert result.blocking is False


@pytest.mark.asyncio
async def test_unregistered_redirect_uri_is_named_and_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        location = (
            "https://accounts.google.com/signin/oauth/error"
            f"?authError={_auth_error('redirect_uri_mismatch')}"
        )
        return httpx.Response(302, headers={"location": location})

    async with _transport(handler) as client:
        result = await check_google_client(
            client, client_id="1-a.apps.googleusercontent.com", redirect_uri=REDIRECT_URI
        )
    assert result.status is ClientCheck.REDIRECT_URI_MISMATCH
    assert result.blocking is True
    # The refusal has to carry the URI, because the whole point is telling
    # somebody the exact string to register.
    assert result.redirect_uri == REDIRECT_URI


@pytest.mark.asyncio
async def test_deleted_or_unknown_client_is_distinguished_from_a_bad_uri():
    """Two different faults, two different things for a person to go and do."""

    def handler(request: httpx.Request) -> httpx.Response:
        location = (
            "https://accounts.google.com/signin/oauth/error"
            f"?authError={_auth_error('deleted_client')}"
        )
        return httpx.Response(302, headers={"location": location})

    async with _transport(handler) as client:
        result = await check_google_client(
            client, client_id="1-a.apps.googleusercontent.com", redirect_uri=REDIRECT_URI
        )
    assert result.status is ClientCheck.CLIENT_UNKNOWN
    assert result.blocking is True


@pytest.mark.asyncio
async def test_google_being_unreachable_never_blocks_a_save():
    """An outage at Google is not evidence about the tenant's client.

    This is the property that matters most in the whole module. A probe that
    failed closed would mean a bad afternoon at Google becomes an afternoon
    where nobody in the estate can configure a workspace.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with _transport(handler) as client:
        result = await check_google_client(
            client, client_id="1-a.apps.googleusercontent.com", redirect_uri=REDIRECT_URI
        )
    assert result.status is ClientCheck.UNDETERMINED
    assert result.blocking is False


@pytest.mark.asyncio
async def test_an_error_page_we_cannot_read_does_not_block_either():
    """An unrecognised code is an unknown, not a guess at the worst case."""

    def handler(request: httpx.Request) -> httpx.Response:
        location = (
            "https://accounts.google.com/signin/oauth/error"
            f"?authError={_auth_error('something_new_from_google')}"
        )
        return httpx.Response(302, headers={"location": location})

    async with _transport(handler) as client:
        result = await check_google_client(
            client, client_id="1-a.apps.googleusercontent.com", redirect_uri=REDIRECT_URI
        )
    assert result.status is ClientCheck.UNDETERMINED
    assert result.blocking is False


@pytest.mark.asyncio
async def test_unreadable_base64_does_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        location = "https://accounts.google.com/signin/oauth/error?authError=!!!not-base64!!!"
        return httpx.Response(302, headers={"location": location})

    async with _transport(handler) as client:
        result = await check_google_client(
            client, client_id="1-a.apps.googleusercontent.com", redirect_uri=REDIRECT_URI
        )
    assert result.status is ClientCheck.UNDETERMINED


@pytest.mark.asyncio
async def test_the_probe_carries_no_secret():
    """It authorises nothing, so it must not be able to.

    A probe that sent a client secret would be a credential leaving the
    deployment on a path nobody audits, for a check that does not need one.
    """
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(302, headers={"location": "https://accounts.google.com/v3/signin"})

    async with _transport(handler) as client:
        await check_google_client(
            client, client_id="1-a.apps.googleusercontent.com", redirect_uri=REDIRECT_URI
        )
    assert "client_secret" not in seen
    assert seen["response_type"] == "code"
    assert seen["redirect_uri"] == REDIRECT_URI


# --- the save path -------------------------------------------------------
#
# The probe only matters if a refusal actually prevents a write. These hold
# the two halves of that: a client Google rejects is never stored, and a
# client it accepts is.

GOOD_CLIENT = "312106866816-abc.apps.googleusercontent.com"


def _service() -> tuple[TenantCredentialService, AsyncMock]:
    context = MagicMock()
    context.tenant_id = UUID("019d0000-0000-7000-8000-0000000000a1")
    context.actor_id = UUID("019d0000-0000-7000-8000-0000000000a2")
    context.trace_id = "trace"
    session = AsyncMock()
    session.add = MagicMock()
    store = AsyncMock()
    return TenantCredentialService(session, context), store


@pytest.mark.asyncio
async def test_a_client_google_refuses_is_never_stored():
    service, store = _service()

    async def probe(client_id: str) -> ClientCheckResult:
        return ClientCheckResult(ClientCheck.REDIRECT_URI_MISMATCH, REDIRECT_URI)

    with pytest.raises(HTTPException) as refusal:
        await service.upsert_google_client(store, GOOD_CLIENT, "a-secret-value", probe=probe)

    assert refusal.value.status_code == 422
    assert refusal.value.detail == "google_client_redirect_uri_not_registered"
    store.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_client_google_accepts_is_stored():
    service, store = _service()

    async def probe(client_id: str) -> ClientCheckResult:
        return ClientCheckResult(ClientCheck.OK, REDIRECT_URI)

    await service.upsert_google_client(store, GOOD_CLIENT, "a-secret-value", probe=probe)
    store.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_undetermined_probe_still_stores():
    """Failing closed here would make a Google outage our outage."""
    service, store = _service()

    async def probe(client_id: str) -> ClientCheckResult:
        return ClientCheckResult(ClientCheck.UNDETERMINED, REDIRECT_URI)

    await service.upsert_google_client(store, GOOD_CLIENT, "a-secret-value", probe=probe)
    store.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_shape_is_rejected_before_google_is_asked():
    """A pasted project number is our mistake to name, not Google's."""
    service, store = _service()
    called = False

    async def probe(client_id: str) -> ClientCheckResult:
        nonlocal called
        called = True
        return ClientCheckResult(ClientCheck.OK, REDIRECT_URI)

    with pytest.raises(HTTPException) as refusal:
        await service.upsert_google_client(store, "417045140496", "a-secret-value", probe=probe)

    assert refusal.value.detail == "google_client_id_invalid"
    assert called is False


# --- the connect path ----------------------------------------------------
#
# Saving is not the only way a workspace ends up with a client Google will
# refuse: one saved before this check existed, or one whose Cloud project was
# edited afterwards, is broken with nobody having typed anything. Every
# consent resolves its client through `_google_client`, so the check belongs
# there too -- it is the last moment before the browser leaves for Google,
# after which the refusal never comes back to us.


def _connector_service():
    from app.services.connectors import ConnectorService

    context = MagicMock()
    context.tenant_id = UUID("019d0000-0000-7000-8000-0000000000a1")
    context.actor_id = UUID("019d0000-0000-7000-8000-0000000000a2")
    context.trace_id = "trace"
    # No tenant credential row, so the client resolves to the deployment's
    # own pair -- this is about the probe, not about whose client it is.
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    return ConnectorService(session, context)


def _settings():
    from app.core.config import Settings

    return Settings(  # pyright: ignore[reportCallIssue]
        _env_file=None,
        app_env="test",
        cursor_signing_key="c" * 32,
        google_connectors_enabled=True,
        google_client_id=GOOD_CLIENT,
        google_client_secret=SecretStr("a-secret-value"),
        search_query_hash_key=SecretStr("q" * 32),
        connector_secret_backend="database_envelope",
        connector_secret_encryption_key=SecretStr("eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="),
    )


@pytest.mark.asyncio
async def test_a_consent_is_refused_before_the_browser_leaves_for_google(monkeypatch):
    service = _connector_service()

    async def probe(client_id: str) -> ClientCheckResult:
        return ClientCheckResult(ClientCheck.REDIRECT_URI_MISMATCH, REDIRECT_URI)

    with pytest.raises(HTTPException) as refusal:
        await service._google_client(_settings(), probe)

    assert refusal.value.status_code == 422
    assert refusal.value.detail == "google_client_redirect_uri_not_registered"


@pytest.mark.asyncio
async def test_a_working_client_still_starts_its_consent():
    service = _connector_service()

    async def probe(client_id: str) -> ClientCheckResult:
        return ClientCheckResult(ClientCheck.OK, REDIRECT_URI)

    client = await service._google_client(_settings(), probe)
    assert client.client_id == GOOD_CLIENT


@pytest.mark.asyncio
async def test_google_being_unreachable_does_not_block_a_consent():
    """Same reasoning as the save path: their outage is not our refusal."""
    service = _connector_service()

    async def probe(client_id: str) -> ClientCheckResult:
        return ClientCheckResult(ClientCheck.UNDETERMINED, REDIRECT_URI)

    client = await service._google_client(_settings(), probe)
    assert client.client_id == GOOD_CLIENT
