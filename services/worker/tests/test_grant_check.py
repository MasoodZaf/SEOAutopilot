"""The daily grant check: renew to prove, and write down what was found.

What matters is which outcome reaches the connection manager. A grant Google
refuses must turn the connector to `reauthorization_required`, because a person
has to act. A grant that merely could not be renewed today -- Google down, a
timeout -- must leave the connector active and only record why, because sending
someone to a consent screen for an outage is the mistake the refresher was built
to avoid.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.connectors.google_oauth import (
    GoogleAuthorizationRevoked,
    GoogleRefreshError,
    RefreshedToken,
)
from app.connectors.grant_check import DUE_SQL, DueGrant, check_grant
from test_gsc_refresh import (  # pyright: ignore[reportMissingImports]
    CONNECTOR,
    KEY,
    KEY_VERSION,
    READONLY,
    SECRET,
    TENANT,
    FakePool,
    FakeRefresher,
    grant,
    sealed,
)


def due() -> DueGrant:
    return DueGrant(
        tenant_id=TENANT,
        connector_id=CONNECTOR,
        connector_type="google_search_console",
        secret_ref=f"db-envelope://{SECRET}",
    )


async def run(pool: FakePool, refresher: Any) -> str:
    async def factory(_tenant):
        return refresher

    return await check_grant(
        pool,  # type: ignore[arg-type]
        due(),
        encryption_key=KEY,
        key_version=KEY_VERSION,
        refresher_factory=factory,
    )


def updates(pool: FakePool) -> list[tuple[str, tuple]]:
    return [
        (sql, args)
        for sql, args in pool.connection.statements
        if "UPDATE connector " in sql or "UPDATE connector\n" in sql
    ]


@pytest.mark.asyncio
async def test_a_good_grant_is_renewed_even_with_time_left() -> None:
    """Renewing is the test, so a token with an hour left is renewed anyway."""
    pool = FakePool(grant(datetime.now(UTC) + timedelta(minutes=50)))
    google = FakeRefresher(
        RefreshedToken("renewed", datetime.now(UTC) + timedelta(hours=1), frozenset({READONLY}))
    )

    assert await run(pool, google) == "ok"
    assert google.calls == ["stored-refresh"]
    stamped = [sql for sql, _ in updates(pool) if "last_checked_at=now()" in sql]
    assert stamped, "a proven grant must record when it was proven"
    cleared = [sql for sql, _ in updates(pool) if "last_error_code=NULL" in sql]
    assert cleared, "a proven grant must clear the previous failure"


@pytest.mark.asyncio
async def test_a_refused_grant_asks_a_person() -> None:
    pool = FakePool(grant(datetime.now(UTC) - timedelta(days=1)))
    google = FakeRefresher(GoogleAuthorizationRevoked("authorization_required"))

    assert await run(pool, google) == "revoked"
    flipped = [args for sql, args in updates(pool) if "reauthorization_required" in sql]
    assert flipped == [(CONNECTOR, TENANT, "authorization_required")]
    assert any(
        "connector.reauthorization_required" in sql for sql, _ in pool.connection.statements
    )


@pytest.mark.asyncio
async def test_google_being_unavailable_does_not_ask_a_person() -> None:
    pool = FakePool(grant(datetime.now(UTC) - timedelta(days=1)))
    google = FakeRefresher(GoogleRefreshError("token_refresh_unavailable"))

    assert await run(pool, google) == "token_refresh_unavailable"
    assert not [sql for sql, _ in updates(pool) if "reauthorization_required" in sql]
    recorded = [args for sql, args in updates(pool) if "last_error_code=$3" in sql]
    assert recorded == [(CONNECTOR, TENANT, "token_refresh_unavailable")]


@pytest.mark.asyncio
async def test_a_grant_without_a_refresh_token_asks_a_person() -> None:
    pool = FakePool(grant(datetime.now(UTC) - timedelta(days=1), refresh_token=None))
    google = FakeRefresher(RuntimeError("must not be called"))

    assert await run(pool, google) == "revoked"
    assert google.calls == []


def test_the_sweep_leaves_alone_what_a_sync_is_about_to_renew() -> None:
    """Two renewals at once collide on the one-active-secret index."""
    assert "status IN ('queued', 'running')" in DUE_SQL
    assert "c.status = 'active'" in DUE_SQL


ANALYTICS = "https://www.googleapis.com/auth/analytics.readonly"


def combined(scopes: list[str]) -> dict:
    return sealed(
        {
            "access_token": "original",
            "refresh_token": "stored-refresh",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "scopes": scopes,
        }
    )


@pytest.mark.asyncio
async def test_one_consent_for_both_services_renews_for_either() -> None:
    """The workspace consent seals the same two-scope grant into both connectors."""
    pool = FakePool(combined([READONLY, ANALYTICS]))
    google = FakeRefresher(
        RefreshedToken(
            "renewed", datetime.now(UTC) + timedelta(hours=1), frozenset({READONLY, ANALYTICS})
        )
    )
    assert await run(pool, google) == "ok"


@pytest.mark.asyncio
async def test_a_grant_reaching_beyond_the_two_services_is_still_refused() -> None:
    pool = FakePool(combined([READONLY, "https://www.googleapis.com/auth/webmasters"]))
    google = FakeRefresher(RuntimeError("must not be called"))

    assert await run(pool, google) == "connector_secret_invalid"
    assert google.calls == []
