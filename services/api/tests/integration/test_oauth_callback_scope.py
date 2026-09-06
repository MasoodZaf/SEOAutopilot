"""The OAuth callback's exception to tenant scoping must stay narrow.

The callback arrives from Google with no session, and the tenant is what the
state row establishes, so one lookup cannot be tenant scoped. That exception is
a SELECT-only policy on `connector_oauth_state` gated by `app.oauth_callback`.
These tests hold it to that shape: read one table, read only, and nothing else
opens up.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


async def _callback_session(app_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    return factory()


@pytest_asyncio.fixture
async def seeded_state(engine, seeded_tenants):
    """One OAuth state row belonging to tenant A."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    tenant_a, _ = seeded_tenants
    state_hash = uuid4().hex * 2
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        site_id = (
            await session.execute(
                text("SELECT id FROM site WHERE tenant_id = :t"), {"t": tenant_a}
            )
        ).scalar_one()
        connector_id = (
            await session.execute(
                text(
                    "INSERT INTO connector (tenant_id, site_id, type, status)"
                    " VALUES (:t, :s, 'google_search_console', 'pending_authorization') RETURNING id"
                ),
                {"t": tenant_a, "s": site_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO connector_oauth_state"
                " (tenant_id, site_id, connector_id, state_hash, requested_scopes,"
                "  requested_property_ref, created_by, expires_at)"
                " VALUES (:t, :s, :c, :h, ARRAY['https://www.googleapis.com/auth/webmasters.readonly'],"
                "  'https://a.example/', :actor, :expires)"
            ),
            {
                "t": tenant_a,
                "s": site_id,
                "c": connector_id,
                "h": state_hash,
                "actor": uuid4(),
                "expires": datetime.now(UTC) + timedelta(minutes=10),
            },
        )
    yield tenant_a, state_hash
    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM connector_oauth_state WHERE state_hash = :h"), {"h": state_hash}
        )
        await session.execute(text("DELETE FROM connector WHERE tenant_id = :t"), {"t": tenant_a})


async def test_the_callback_can_resolve_a_state_row_without_a_tenant_scope(
    app_engine, seeded_state
) -> None:
    _, state_hash = seeded_state
    session = await _callback_session(app_engine)
    async with session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))
        found = (
            await session.execute(
                text("SELECT tenant_id FROM connector_oauth_state WHERE state_hash = :h"),
                {"h": state_hash},
            )
        ).scalar_one_or_none()
    assert found is not None


async def test_without_the_callback_flag_the_state_row_is_invisible(
    app_engine, seeded_state
) -> None:
    _, state_hash = seeded_state
    session = await _callback_session(app_engine)
    async with session, session.begin():
        found = (
            await session.execute(
                text("SELECT tenant_id FROM connector_oauth_state WHERE state_hash = :h"),
                {"h": state_hash},
            )
        ).scalar_one_or_none()
    assert found is None


async def test_the_callback_flag_does_not_open_any_other_table(
    app_engine, seeded_state
) -> None:
    """The exception is one table. Sites and connectors stay closed."""
    session = await _callback_session(app_engine)
    async with session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))
        sites = (await session.execute(text("SELECT count(*) FROM site"))).scalar_one()
        connectors = (await session.execute(text("SELECT count(*) FROM connector"))).scalar_one()
    assert (sites, connectors) == (0, 0)


async def test_the_callback_flag_grants_no_write_access(app_engine, engine, seeded_state) -> None:
    """SELECT only: the callback cannot consume state before adopting a scope.

    Row-level security does not raise on an UPDATE that matches nothing -- the
    policy simply makes no row visible to the command, so it writes zero rows.
    The assertion is therefore that the state survives unconsumed, which is the
    property that matters: state can only be spent from inside a tenant scope.
    """
    _, state_hash = seeded_state
    session = await _callback_session(app_engine)
    async with session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))
        result = await session.execute(
            text("UPDATE connector_oauth_state SET consumed_at = now() WHERE state_hash = :h"),
            {"h": state_hash},
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]

    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as owner, owner.begin():
        consumed = (
            await owner.execute(
                text("SELECT consumed_at FROM connector_oauth_state WHERE state_hash = :h"),
                {"h": state_hash},
            )
        ).scalar_one()
    assert consumed is None


async def test_adopting_the_rows_tenant_scope_reopens_the_rest(app_engine, seeded_state) -> None:
    """What the callback actually does: resolve, adopt the scope, then proceed."""
    tenant_a, state_hash = seeded_state
    session = await _callback_session(app_engine)
    async with session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))
        resolved = (
            await session.execute(
                text("SELECT tenant_id FROM connector_oauth_state WHERE state_hash = :h"),
                {"h": state_hash},
            )
        ).scalar_one()
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(resolved)}
        )
        sites = (await session.execute(text("SELECT count(*) FROM site"))).scalar_one()
        locked = (
            await session.execute(
                text(
                    "SELECT tenant_id FROM connector_oauth_state"
                    " WHERE state_hash = :h AND tenant_id = :t FOR UPDATE"
                ),
                {"h": state_hash, "t": resolved},
            )
        ).scalar_one_or_none()
    assert resolved == tenant_a
    assert sites == 1
    assert locked == tenant_a
