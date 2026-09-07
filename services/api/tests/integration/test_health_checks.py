"""The invariants that look exactly like normal operation from outside.

Each of these has happened on this deployment or is one restart away, and none
of them shows up in a health endpoint: a run leased to a worker that died, an
outbox nobody is draining, a connector that has been asking for re-consent for
days, a rollback nothing has ever looked at.

They are counting queries against real tables, so they are tested against
PostgreSQL. A check that silently counts nothing -- a typo in a column, a
predicate that never matches -- passes forever and reports health it never
measured, which is the failure worth ruling out.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli.health import run_checks
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


async def checks(database_url, **overrides):
    options = {
        "backup_dir": None,
        "backup_max_age_hours": 36,
        "outbox_backlog_minutes": 15,
        "stale_rollback_days": 7,
        "stale_connector_days": 3,
    }
    options.update(overrides)
    found = await run_checks(database_url, **options)
    return {check.name: check for check in found}


@pytest_asyncio.fixture
async def quiet_tenant(engine):
    """A tenant with nothing wrong with it, removed afterwards."""
    tenant_id = uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenant (id, slug, name, status)"
                " VALUES (:id, :slug, 'health', 'active')"
            ),
            {"id": tenant_id, "slug": f"health-{tenant_id.hex[:8]}"},
        )
    yield tenant_id, factory
    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM outbox_event WHERE tenant_id = :id"), {"id": tenant_id}
        )
        await session.execute(text("DELETE FROM tenant WHERE id = :id"), {"id": tenant_id})


async def test_every_check_runs_and_reports_a_number(database_url, quiet_tenant) -> None:
    """A check that never matches anything reports health it did not measure."""
    found = await checks(database_url)
    assert set(found) == {
        "stale_leases",
        "outbox_backlog",
        "connectors_needing_attention",
        "unreconciled_rollbacks",
        "rollbacks_awaiting_a_decision",
        "tenants_without_an_owner",
        "connectors_not_syncing",
    }
    for check in found.values():
        assert check.value >= 0
        assert check.detail


async def test_an_undrained_outbox_is_noticed(database_url, quiet_tenant) -> None:
    tenant_id, factory = quiet_tenant
    found = await checks(database_url)
    before = found["outbox_backlog"].value

    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO outbox_event"
                " (tenant_id, event_type, event_version, aggregate_type, aggregate_id,"
                "  payload, occurred_at)"
                " VALUES (:id, 'test.stale', 1, 'proposal', :aggregate, '{}'::jsonb, :when)"
            ),
            {
                "id": tenant_id,
                "aggregate": uuid4(),
                "when": datetime.now(UTC) - timedelta(hours=2),
            },
        )

    after = await checks(database_url)
    assert after["outbox_backlog"].value == before + 1
    assert after["outbox_backlog"].failing


async def test_a_recent_event_is_not_a_backlog(database_url, quiet_tenant) -> None:
    """The worker is allowed to be a moment behind; that is not an outage."""
    tenant_id, factory = quiet_tenant
    before = (await checks(database_url))["outbox_backlog"].value

    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO outbox_event"
                " (tenant_id, event_type, event_version, aggregate_type, aggregate_id, payload)"
                " VALUES (:id, 'test.fresh', 1, 'proposal', :aggregate, '{}'::jsonb)"
            ),
            {"id": tenant_id, "aggregate": uuid4()},
        )

    assert (await checks(database_url))["outbox_backlog"].value == before


async def test_a_backup_directory_that_stopped_being_written_to_fails(
    database_url, tmp_path: Path
) -> None:
    """An empty or stale backup directory looks exactly like a working one."""
    empty = await checks(database_url, backup_dir=tmp_path)
    assert empty["backup_age_hours"].failing
    assert "no dumps" in empty["backup_age_hours"].detail

    dump = tmp_path / "seo_autopilot_20260101T000000Z.dump"
    dump.write_bytes(b"not really a dump")
    stale = datetime.now(UTC) - timedelta(days=20)
    import os

    os.utime(dump, (stale.timestamp(), stale.timestamp()))
    assert (await checks(database_url, backup_dir=tmp_path))["backup_age_hours"].failing


async def test_a_fresh_backup_passes(database_url, tmp_path: Path) -> None:
    (tmp_path / "seo_autopilot_20260907T000000Z.dump").write_bytes(b"dump")
    found = await checks(database_url, backup_dir=tmp_path)
    assert not found["backup_age_hours"].failing


async def test_a_tenant_that_lost_its_last_owner_is_noticed(database_url, quiet_tenant) -> None:
    """The state the membership rules exist to prevent, arrived at another way.

    `MembershipService` will not demote or remove a last owner, and locks the
    rows it counts so two concurrent demotions cannot both commit. None of that
    covers a row written directly, or a restore from a dump taken mid-change.
    The result is a tenant nobody in it can administer, and it looks entirely
    healthy from outside.
    """
    tenant_id, factory = quiet_tenant
    before = (await checks(database_url))["tenants_without_an_owner"].value

    user_id = uuid4()
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO app_user (id, subject, issuer, email, email_normalized)"
                " VALUES (:id, :subject, 'https://issuer.test', :email, :email)"
            ),
            {"id": user_id, "subject": f"s-{user_id.hex[:8]}", "email": f"{user_id.hex[:8]}@t.test"},
        )
        await session.execute(
            text(
                "INSERT INTO tenant_membership (tenant_id, user_id, role, status)"
                " VALUES (:tenant, :user, 'admin', 'active')"
            ),
            {"tenant": tenant_id, "user": user_id},
        )

    after = await checks(database_url)
    assert after["tenants_without_an_owner"].value == before + 1
    assert after["tenants_without_an_owner"].failing

    # ...and promoting somebody back clears it, so this is measuring the
    # condition rather than counting tenants that happen to have members.
    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE tenant_membership SET role = 'owner' WHERE tenant_id = :id"),
            {"id": tenant_id},
        )
    assert (await checks(database_url))["tenants_without_an_owner"].value == before

    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM tenant_membership WHERE tenant_id = :id"), {"id": tenant_id}
        )
        await session.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})


async def test_a_tenant_with_no_members_at_all_is_not_ownerless(
    database_url, quiet_tenant
) -> None:
    """A tenant nobody has joined yet is not a tenant that lost its owner.

    `quiet_tenant` is exactly that, and counting it would make this check fire
    on every freshly created tenant until somebody was invited -- noise that
    would get the whole timer muted.
    """
    assert not (await checks(database_url))["tenants_without_an_owner"].failing


async def test_an_active_connector_that_has_never_synced_is_noticed(
    database_url, quiet_tenant
) -> None:
    """Active, no error, and reading nothing.

    `connectors_needing_attention` sees the honest failures -- the provider
    refused, the row moved to `error`. This is the quiet one: an authorization
    that returned no refresh token works for an hour and then stops, and nothing
    about the connector row changes to say so.
    """
    tenant_id, factory = quiet_tenant
    before = (await checks(database_url))["connectors_not_syncing"].value

    site_id, connector_id = uuid4(), uuid4()
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO site (id, tenant_id, name, canonical_origin, normalized_host,"
                " status, verified_at) VALUES (:id, :tenant, 'health', 'https://h.test',"
                " :host, 'active', now())"
            ),
            {"id": site_id, "tenant": tenant_id, "host": f"{site_id.hex[:8]}.test"},
        )
        await session.execute(
            text(
                "INSERT INTO connector (id, tenant_id, site_id, type, status, created_at)"
                " VALUES (:id, :tenant, :site, 'google_analytics', 'active',"
                "         now() - interval '10 days')"
            ),
            {"id": connector_id, "tenant": tenant_id, "site": site_id},
        )

    after = await checks(database_url)
    assert after["connectors_not_syncing"].value == before + 1
    assert after["connectors_not_syncing"].failing

    # A sync today clears it.
    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE connector SET last_sync_at = now() WHERE id = :id"), {"id": connector_id}
        )
    assert (await checks(database_url))["connectors_not_syncing"].value == before

    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM connector WHERE id = :id"), {"id": connector_id})
        await session.execute(text("DELETE FROM site WHERE id = :id"), {"id": site_id})


async def test_a_connector_authorized_today_is_given_time_to_sync(
    database_url, quiet_tenant
) -> None:
    """A connector connected an hour ago has not failed to sync; it is new.

    Without this the check would fire on every connector between consent and
    its first scheduled run -- which is precisely the window an operator is
    watching it in, and the moment a false alarm does the most damage to trust
    in the check.
    """
    tenant_id, factory = quiet_tenant
    before = (await checks(database_url))["connectors_not_syncing"].value

    site_id, connector_id = uuid4(), uuid4()
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO site (id, tenant_id, name, canonical_origin, normalized_host,"
                " status, verified_at) VALUES (:id, :tenant, 'health', 'https://h.test',"
                " :host, 'active', now())"
            ),
            {"id": site_id, "tenant": tenant_id, "host": f"{site_id.hex[:8]}.test"},
        )
        await session.execute(
            text(
                "INSERT INTO connector (id, tenant_id, site_id, type, status)"
                " VALUES (:id, :tenant, :site, 'google_analytics', 'active')"
            ),
            {"id": connector_id, "tenant": tenant_id, "site": site_id},
        )

    assert (await checks(database_url))["connectors_not_syncing"].value == before

    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM connector WHERE id = :id"), {"id": connector_id})
        await session.execute(text("DELETE FROM site WHERE id = :id"), {"id": site_id})
