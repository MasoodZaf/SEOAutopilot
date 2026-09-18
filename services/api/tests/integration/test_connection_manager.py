"""Listing and disconnecting connectors, against PostgreSQL with RLS on.

Disconnect is the one destructive action on the connection manager, so the
things worth proving are the ones a mocked session cannot show: the sealed
secret is really unreadable afterwards, the row survives the CHECK constraints
in its new state, another tenant's connector is out of reach, and a
disconnected connector can be connected again.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.routes.connectors import grant_expires_at
from app.core.context import Role, TenantContext
from app.services.connectors import ConnectorService
from tests.conftest import requires_database
from tests.integration import test_github_connector as github
from tests.integration.test_github_connector import connect, scoped, writable

# Two committed tenants with one verified site each, borrowed rather than
# rebuilt so both files seed and tear down the same way.
acme = github.acme
other = github.other

pytestmark = [pytest.mark.asyncio, requires_database]


def connectors(session, ids, role: Role = Role.OWNER) -> ConnectorService:
    return ConnectorService(
        session,
        TenantContext(
            tenant_id=ids["tenant_id"], actor_id=ids["actor_id"], role=role, trace_id="t"
        ),
    )


async def active_secrets(session, connector_id) -> int:
    return await session.scalar(
        text(
            "SELECT count(*) FROM connector_secret"
            " WHERE connector_id=:id AND revoked_at IS NULL"
        ),
        {"id": connector_id},
    )


async def test_disconnect_destroys_the_stored_credential(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        connector = await connect(session, acme, writable())
        assert await active_secrets(session, connector.id) == 1

        result = await connectors(session, acme).disconnect(connector.id)
        await session.flush()

        assert result.status == "revoked"
        assert result.secret_ref is None
        assert await active_secrets(session, connector.id) == 0
        audited = await session.scalar(
            text(
                "SELECT count(*) FROM audit_event"
                " WHERE resource_id=:id AND action='connector.revoked'"
            ),
            {"id": str(connector.id)},
        )
        assert audited == 1


async def test_disconnecting_twice_changes_nothing(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        connector = await connect(session, acme, writable())
        first = await connectors(session, acme).disconnect(connector.id)
        version = first.version
        second = await connectors(session, acme).disconnect(connector.id)
        assert second.status == "revoked"
        assert second.version == version


async def test_a_disconnected_connector_can_be_connected_again(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        connector = await connect(session, acme, writable())
        await connectors(session, acme).disconnect(connector.id)
        await session.flush()

        again = await connect(session, acme, writable())
        await session.flush()

        assert again.id == connector.id
        assert again.status == "active"
        assert await active_secrets(session, connector.id) == 1


async def test_only_an_owner_or_admin_can_disconnect(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        connector = await connect(session, acme, writable())
        with pytest.raises(HTTPException) as refused:
            await connectors(session, acme, Role.SEO_MANAGER).disconnect(connector.id)
        assert refused.value.status_code == 403


async def test_another_tenants_connector_is_not_found(app_engine, acme, other) -> None:
    async with scoped(app_engine, other["tenant_id"]) as session:
        theirs = await connect(session, other, writable())
        await session.commit()
    try:
        async with scoped(app_engine, acme["tenant_id"]) as session:
            with pytest.raises(HTTPException) as refused:
                await connectors(session, acme).disconnect(theirs.id)
            assert refused.value.status_code == 404
            listed = await connectors(session, acme).list_for_tenant()
            assert listed == []
    finally:
        async with scoped(app_engine, other["tenant_id"]) as session:
            await session.execute(
                text("DELETE FROM connector_secret WHERE connector_id=:id"), {"id": theirs.id}
            )
            await session.execute(
                text("DELETE FROM connector WHERE id=:id"), {"id": theirs.id}
            )
            await session.commit()


async def test_the_workspace_listing_carries_each_connectors_site(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        connector = await connect(session, acme, writable())
        listed = await connectors(session, acme).list_for_tenant()
        assert [(c.id, s.normalized_host) for c, s in listed] == [
            (connector.id, "acme.example")
        ]


async def test_a_google_grant_expires_only_when_a_lifetime_is_declared() -> None:
    consented = datetime(2026, 9, 6, tzinfo=UTC)
    google = SimpleNamespace(
        type="google_search_console", consented_at=consented, status="active"
    )
    assert grant_expires_at(google, None) is None
    assert grant_expires_at(google, 7) == consented + timedelta(days=7)
    # Still shown once it has lapsed, so the page can say when it happened.
    lapsed = SimpleNamespace(**{**vars(google), "status": "reauthorization_required"})
    assert grant_expires_at(lapsed, 7) == consented + timedelta(days=7)
    # Nothing is held, so nothing expires.
    revoked = SimpleNamespace(**{**vars(google), "status": "revoked"})
    assert grant_expires_at(revoked, 7) is None
    repository = SimpleNamespace(**{**vars(google), "type": "github_repository"})
    assert grant_expires_at(repository, 7) is None
