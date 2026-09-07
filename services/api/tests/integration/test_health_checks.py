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
