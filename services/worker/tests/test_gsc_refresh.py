"""Renewing a Search Console grant instead of asking a person to.

A Google access token lives one hour. The consent callback stored the refresh
token that renews it and nothing ever read that field, so the sync worker
refused the moment the access token expired and flipped the connector to
`reauthorization_required`. Search Console data could not accumulate past the
first hour after a consent, which is why `search_metric` was empty.

The cases that matter are the ones where the wrong answer costs a person
something: a transient Google failure must not be reported as a revoked grant,
and a renewal must not lose the refresh token that makes the next one possible.
"""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from app.connectors.google_oauth import (
    GoogleAuthorizationRevoked,
    GoogleRefreshError,
    GoogleTokenHttpRefresher,
    RefreshedToken,
)
from app.connectors.runtime import (
    AccessTokenManager,
    ClaimedSync,
    decrypt_secret_payload,
    secret_aad,
)
from app.gsc.client import SearchAnalyticsRow, SearchConsoleError
from app.gsc.consumer import CredentialedSearchConsole
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
KEY = b"k" * 32
KEY_VERSION = "test-v1"
TENANT = UUID("019d0000-0000-7000-8000-000000000011")
CONNECTOR = UUID("019d0000-0000-7000-8000-000000000061")
SECRET = UUID("019d0000-0000-7000-8000-000000000071")
NEW_SECRET = UUID("019d0000-0000-7000-8000-000000000072")


def refresher(handler) -> GoogleTokenHttpRefresher:
    return GoogleTokenHttpRefresher(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        client_id="client",
        client_secret="secret",
    )


# --------------------------------------------------------------------------
# Talking to Google


@pytest.mark.asyncio
async def test_a_refresh_returns_a_token_and_when_it_dies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(pair.split("=") for pair in request.content.decode().split("&"))
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "stored-refresh"
        return httpx.Response(
            200,
            json={"access_token": "renewed", "expires_in": 3600, "scope": READONLY},
        )

    before = datetime.now(UTC)
    renewed = await refresher(handler).refresh("stored-refresh")

    assert renewed.access_token == "renewed"
    assert renewed.scopes == frozenset({READONLY})
    assert timedelta(minutes=55) < renewed.expires_at - before < timedelta(minutes=65)


@pytest.mark.asyncio
async def test_a_revoked_grant_is_the_only_thing_that_asks_for_a_person() -> None:
    handler = lambda _: httpx.Response(400, json={"error": "invalid_grant"})
    with pytest.raises(GoogleAuthorizationRevoked, match="authorization_required"):
        await refresher(handler).refresh("stored-refresh")


@pytest.mark.asyncio
async def test_google_being_unwell_is_not_a_revoked_grant() -> None:
    """The distinction the whole design turns on.

    Reporting a 500, a timeout, or a malformed request as a revoked grant would
    mark the connector `reauthorization_required` and send the tenant to a
    consent screen to fix an outage they had nothing to do with.
    """
    cases: list[Any] = [
        lambda _: httpx.Response(500, json={}),
        lambda _: httpx.Response(429, json={}),
        lambda _: httpx.Response(400, json={"error": "invalid_request"}),
        lambda _: httpx.Response(200, json={"access_token": "x"}),
        lambda _: httpx.Response(200, json={"access_token": "x", "expires_in": 0}),
    ]
    for handler in cases:
        with pytest.raises(GoogleRefreshError):
            await refresher(handler).refresh("stored-refresh")


@pytest.mark.asyncio
async def test_a_network_failure_is_transient_not_terminal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(GoogleRefreshError, match="token_refresh_unavailable"):
        await refresher(handler).refresh("stored-refresh")


@pytest.mark.asyncio
async def test_a_response_without_a_scope_claims_no_scopes() -> None:
    """Google omits `scope` on a refresh when nothing changed.

    Reading that as "the grant now has no scopes" would make the caller refuse
    a grant that is exactly what was consented to.
    """
    handler = lambda _: httpx.Response(
        200, json={"access_token": "renewed", "expires_in": 3600}
    )
    assert (await refresher(handler).refresh("r")).scopes == frozenset()


# --------------------------------------------------------------------------
# Reading, renewing and re-sealing the stored grant


def sealed(payload: dict[str, object], *, provider: str = "google_search_console") -> dict:
    aad = secret_aad(TENANT, CONNECTOR, provider, KEY_VERSION)
    nonce = b"n" * 12
    return {
        "provider": provider,
        "ciphertext": AESGCM(KEY).encrypt(nonce, json.dumps(payload).encode(), aad),
        "nonce": nonce,
        "aad_hash": hashlib.sha256(aad).hexdigest(),
        "key_version": KEY_VERSION,
    }


def grant(expires_at: datetime, *, refresh_token: str | None = "stored-refresh") -> dict:
    payload: dict[str, object] = {
        "access_token": "original",
        "expires_at": expires_at.isoformat(),
        "scopes": [READONLY],
    }
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    return sealed(payload)


class FakeConnection:
    def __init__(self, row: dict | None) -> None:
        self.row = row
        self.statements: list[tuple[str, tuple]] = []

    def transaction(self):
        return _NullContext()

    async def execute(self, sql: str, *args: Any) -> None:
        self.statements.append((sql, args))

    async def fetchrow(self, sql: str, *args: Any):
        self.statements.append((sql, args))
        return self.row

    async def fetchval(self, sql: str, *args: Any):
        self.statements.append((sql, args))
        return NEW_SECRET


class _NullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, row: dict | None) -> None:
        self.connection = FakeConnection(row)

    def acquire(self):
        pool = self

        class _Acquire:
            async def __aenter__(self):
                return pool.connection

            async def __aexit__(self, *exc):
                return False

        return _Acquire()

    def inserted_secret(self) -> tuple:
        for sql, args in self.connection.statements:
            if "INSERT INTO connector_secret" in sql:
                return args
        raise AssertionError("no secret was written")


def claimed() -> ClaimedSync:
    return ClaimedSync(
        id=uuid4(),
        tenant_id=TENANT,
        connector_id=CONNECTOR,
        site_id=uuid4(),
        property_ref="sc-domain:example.com",
        secret_ref=f"db-envelope://{SECRET}",
        range_start=date(2026, 8, 1),
        range_end=date(2026, 8, 2),
        raw_cursor={"day": "2026-08-01", "start_row": 0},
        base_days_completed=0,
        base_rows_seen=0,
        base_rows_upserted=0,
        base_rows_new=0,
    )


class FakeRefresher:
    def __init__(self, result: RefreshedToken | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    async def refresh(self, refresh_token: str) -> RefreshedToken:
        self.calls.append(refresh_token)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def manager(pool: FakePool, token_refresher: Any) -> AccessTokenManager:
    return AccessTokenManager(
        pool,  # type: ignore[arg-type]
        claimed(),
        encryption_key=KEY,
        key_version=KEY_VERSION,
        refresher=token_refresher,
        expected_scopes=frozenset({READONLY}),
    )


@pytest.mark.asyncio
async def test_a_token_with_time_left_is_used_as_it_is() -> None:
    pool = FakePool(grant(datetime.now(UTC) + timedelta(minutes=50)))
    google = FakeRefresher(RefreshedToken("unused", datetime.now(UTC), frozenset()))

    assert await manager(pool, google).token() == "original"
    assert google.calls == []


@pytest.mark.asyncio
async def test_an_expired_token_is_renewed_rather_than_refused() -> None:
    """The whole defect, in one case.

    Before this, an expired access token raised `authorization_required` and
    the connector was marked as needing a human. Now it is renewed.
    """
    pool = FakePool(grant(datetime.now(UTC) - timedelta(minutes=5)))
    expiry = datetime.now(UTC) + timedelta(hours=1)
    google = FakeRefresher(RefreshedToken("renewed", expiry, frozenset({READONLY})))

    assert await manager(pool, google).token() == "renewed"
    assert google.calls == ["stored-refresh"]


@pytest.mark.asyncio
async def test_a_renewal_keeps_the_refresh_token_for_the_next_one() -> None:
    """Google does not reissue a refresh token, so it has to be carried over.

    Dropping it would work exactly once and then reproduce the original bug an
    hour later, with the added confusion of having been "fixed".
    """
    pool = FakePool(grant(datetime.now(UTC) - timedelta(minutes=5)))
    expiry = datetime.now(UTC) + timedelta(hours=1)
    await manager(
        pool, FakeRefresher(RefreshedToken("renewed", expiry, frozenset({READONLY})))
    ).token()

    _tenant, _connector, provider, ciphertext, nonce, aad_hash, key_version = (
        pool.inserted_secret()
    )
    payload = decrypt_secret_payload(
        tenant_id=TENANT,
        connector_id=CONNECTOR,
        provider=provider,
        key_version=key_version,
        ciphertext=ciphertext,
        nonce=nonce,
        aad_hash=aad_hash,
        encryption_key=KEY,
    )

    assert payload["access_token"] == "renewed"
    assert payload["refresh_token"] == "stored-refresh"
    assert payload["scopes"] == [READONLY]
    assert datetime.fromisoformat(str(payload["expires_at"])) == expiry


@pytest.mark.asyncio
async def test_the_old_secret_is_retired_and_the_connector_repointed() -> None:
    pool = FakePool(grant(datetime.now(UTC) - timedelta(minutes=5)))
    await manager(
        pool,
        FakeRefresher(
            RefreshedToken("renewed", datetime.now(UTC) + timedelta(hours=1), frozenset())
        ),
    ).token()

    statements = " ".join(sql for sql, _ in pool.connection.statements)
    assert "UPDATE connector_secret SET revoked_at=now()" in statements
    repoint = next(
        args for sql, args in pool.connection.statements if "SET secret_ref=" in sql
    )
    assert repoint[2] == f"db-envelope://{NEW_SECRET}"


@pytest.mark.asyncio
async def test_a_missing_client_secret_is_not_reported_as_a_revoked_grant() -> None:
    """A server misconfiguration must not be laid at the tenant's door.

    `authorization_required` marks the connector as needing re-consent. Doing
    that when the server has no client secret sends someone to click through
    Google to fix a problem clicking cannot fix.
    """
    pool = FakePool(grant(datetime.now(UTC) - timedelta(minutes=5)))
    with pytest.raises(ValueError, match="token_refresh_not_configured"):
        await manager(pool, None).token()


@pytest.mark.asyncio
async def test_a_stored_grant_with_no_refresh_token_needs_a_person() -> None:
    pool = FakePool(grant(datetime.now(UTC) + timedelta(hours=1), refresh_token=None))
    with pytest.raises(GoogleAuthorizationRevoked, match="authorization_required"):
        await manager(pool, FakeRefresher(RefreshedToken("x", datetime.now(UTC), frozenset()))).token()


@pytest.mark.asyncio
async def test_a_renewal_that_changes_the_scopes_is_refused() -> None:
    """Writing back a grant nobody consented to would widen this connector."""
    pool = FakePool(grant(datetime.now(UTC) - timedelta(minutes=5)))
    widened = RefreshedToken(
        "renewed",
        datetime.now(UTC) + timedelta(hours=1),
        frozenset({READONLY, "https://www.googleapis.com/auth/webmasters"}),
    )
    with pytest.raises(GoogleAuthorizationRevoked):
        await manager(pool, FakeRefresher(widened)).token()


# --------------------------------------------------------------------------
# Renewing partway through a backfill


class FakeSearchConsole:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.tokens: list[str] = []

    async def query_day(self, property_ref: str, access_token: str, day, start_row: int):
        self.tokens.append(access_token)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeTokens:
    def __init__(self) -> None:
        self.issued = ["first", "second"]
        self.renewals = 0

    async def token(self) -> str:
        return self.issued[0]

    async def renew(self) -> str:
        self.renewals += 1
        self.issued.pop(0)
        return self.issued[0]


@pytest.mark.asyncio
async def test_a_token_that_expires_mid_backfill_is_renewed_and_the_page_retried() -> None:
    """A 490-day backfill outlives a one-hour token.

    Without this the run dies partway, the day's rows are lost, and a healthy
    connector is marked as needing re-consent.
    """
    rows = [SearchAnalyticsRow("q", "https://example.com/a", "usa", "mobile", 1, 10, 0.1, 8)]
    client = FakeSearchConsole([SearchConsoleError("authorization_required"), rows])
    tokens = FakeTokens()

    result = await CredentialedSearchConsole(
        client,  # type: ignore[arg-type]
        tokens,  # type: ignore[arg-type]
    ).query_day("sc-domain:example.com", date(2026, 8, 1), 0)

    assert result == rows
    assert tokens.renewals == 1
    assert client.tokens == ["first", "second"]


@pytest.mark.asyncio
async def test_an_error_that_is_not_about_authorization_is_not_retried() -> None:
    """Retrying a rate limit with a new token just spends the quota twice."""
    client = FakeSearchConsole([SearchConsoleError("provider_rate_limited")])
    tokens = FakeTokens()

    with pytest.raises(SearchConsoleError, match="provider_rate_limited"):
        await CredentialedSearchConsole(
            client,  # type: ignore[arg-type]
            tokens,  # type: ignore[arg-type]
        ).query_day("sc-domain:example.com", date(2026, 8, 1), 0)

    assert tokens.renewals == 0
