"""Idempotency keys under concurrency, against PostgreSQL.

Four service methods share one shape: look for a row carrying this idempotency
key, and if there is none, build one. Two requests with the same key that
overlap in time both find nothing, both build, and the unique constraint rejects
the second -- so a caller who retried gets a 500 instead of the thing their
first attempt already made.

`ProposalService.deploy_proposal` was the dangerous one, because it also called
the deployment adapter before inserting, so the race opened two pull requests
for one deployment; that case lives in `test_deployment_gate.py`. This file
covers the rest, where the cost is a wrong answer rather than a duplicated
change to a customer's site.

None of this is observable with a mocked session, which has no constraints and
no second connection to race against.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.schemas import ConnectorSyncCreate
from app.core.context import Role, TenantContext
from app.services.connectors import ConnectorService
from app.services.performance import PerformanceService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


@pytest_asyncio.fixture
async def connector(engine):
    """An active GSC connector on a verified site, committed then removed."""
    ids = {name: uuid4() for name in ("tenant_id", "site_id", "connector_id", "actor_id")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'sync','active')"),
            {"id": ids["tenant_id"], "slug": f"sync-{ids['tenant_id'].hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,"
                "mode,status,verified_at)"
                " VALUES(:id,:tenant_id,'s','https://sync.example','sync.example',"
                "'observe','active',now())"
            ),
            {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
        )
        await session.execute(
            text(
                "INSERT INTO connector(id,tenant_id,site_id,type,status,secret_ref,"
                "external_account_ref)"
                " VALUES(:id,:tenant_id,:site_id,'google_search_console','active',"
                "'secret://ref','sc-domain:sync.example')"
            ),
            {
                "id": ids["connector_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
            },
        )
    yield ids
    async with factory() as session, session.begin():
        for table in ("connector_sync", "outbox_event", "audit_event", "connector", "site"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"),
                {"tenant_id": ids["tenant_id"]},
            )
        await session.execute(
            text("DELETE FROM tenant WHERE id=:id"), {"id": ids["tenant_id"]}
        )


def sync_command() -> ConnectorSyncCreate:
    today = datetime.now(UTC).date()
    return ConnectorSyncCreate(
        kind="backfill", range_start=today - timedelta(days=7), range_end=today - timedelta(days=3)
    )


async def request_sync(session, ids: dict[str, UUID], key: str):
    service = ConnectorService(
        session,
        TenantContext(
            tenant_id=ids["tenant_id"],
            actor_id=ids["actor_id"],
            role=Role.OWNER,
            trace_id="integration",
        ),
    )
    return await service.create_sync(ids["connector_id"], sync_command(), key)


async def test_two_concurrent_sync_requests_create_one_sync(app_engine, connector) -> None:
    """The loser of the race is owed the sync its twin made, not a 500."""
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    async def attempt():
        async with factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(connector["tenant_id"])},
            )
            sync = await request_sync(session, connector, "race-key-000001")
            # Read the id before the transaction ends; the object expires after.
            return sync.id

    first, second = await asyncio.gather(attempt(), attempt())
    assert first == second

    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(connector["tenant_id"])},
        )
        assert (
            await session.execute(
                text(
                    "SELECT count(*) FROM connector_sync"
                    " WHERE tenant_id=:tenant_id AND idempotency_key='race-key-000001'"
                ),
                {"tenant_id": connector["tenant_id"]},
            )
        ).scalar_one() == 1


async def test_a_sequential_retry_returns_the_same_sync(app_engine, connector) -> None:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(connector["tenant_id"])},
        )
        first = await request_sync(session, connector, "serial-key-0001")
        second = await request_sync(session, connector, "serial-key-0001")
        assert first.id == second.id


async def test_different_keys_create_different_syncs(app_engine, connector) -> None:
    """The guard must not collapse two genuinely distinct requests into one."""
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(connector["tenant_id"])},
        )
        first = await request_sync(session, connector, "distinct-key-01")
        second = await request_sync(session, connector, "distinct-key-02")
        assert first.id != second.id


@pytest_asyncio.fixture
async def performance_site(engine):
    """A verified site with one completed crawl and a 200 observation."""
    ids = {
        name: uuid4()
        for name in ("tenant_id", "site_id", "page_id", "crawl_id", "actor_id")
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'perf','active')"),
            {"id": ids["tenant_id"], "slug": f"perf-{ids['tenant_id'].hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,"
                "mode,status,verified_at)"
                " VALUES(:id,:tenant_id,'p','https://perf.example','perf.example',"
                "'observe','active',now())"
            ),
            {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
        )
        await session.execute(
            text(
                "INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash)"
                " VALUES(:id,:tenant_id,:site_id,'https://perf.example/',:url_hash)"
            ),
            {
                "id": ids["page_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "url_hash": sha256(b"https://perf.example/").hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status)"
                " VALUES(:id,:tenant_id,:site_id,:actor,'{}'::jsonb,'completed')"
            ),
            {
                "id": ids["crawl_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "actor": ids["actor_id"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO page_observation(tenant_id,page_id,crawl_job_id,http_status,"
                "final_url,word_count,content_hash)"
                " VALUES(:tenant_id,:page_id,:crawl_id,200,'https://perf.example/',400,:hash)"
            ),
            {
                "tenant_id": ids["tenant_id"],
                "page_id": ids["page_id"],
                "crawl_id": ids["crawl_id"],
                "hash": sha256(b"body").hexdigest(),
            },
        )
    yield ids
    async with factory() as session, session.begin():
        for table in (
            "performance_observation", "performance_run", "outbox_event", "audit_event",
            "page_observation", "page", "crawl_job", "site",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"),
                {"tenant_id": ids["tenant_id"]},
            )
        await session.execute(
            text("DELETE FROM tenant WHERE id=:id"), {"id": ids["tenant_id"]}
        )


async def test_two_concurrent_performance_requests_return_one_run(
    app_engine, performance_site
) -> None:
    """A racing retry must get its run back, not "another run is already active".

    `performance_run` carries two unique constraints, and before this they were
    reported identically. The partial index really does mean another run is in
    flight; the idempotency key means this caller is retrying their own request
    and is owed its result. Answering "already active" to the second is both
    untrue and unrecoverable, because the run it names is their own.
    """
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    async def attempt():
        async with factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(performance_site["tenant_id"])},
            )
            service = PerformanceService(
                session,
                TenantContext(
                    tenant_id=performance_site["tenant_id"],
                    actor_id=performance_site["actor_id"],
                    role=Role.OWNER,
                    trace_id="integration",
                ),
            )
            run = await service.create_run(performance_site["site_id"], "perf-race-0001")
            return run.id

    first, second = await asyncio.gather(attempt(), attempt())
    assert first == second

    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(performance_site["tenant_id"])},
        )
        assert (
            await session.execute(
                text("SELECT count(*) FROM performance_run WHERE tenant_id=:tenant_id"),
                {"tenant_id": performance_site["tenant_id"]},
            )
        ).scalar_one() == 1


async def test_a_second_run_under_a_different_key_is_still_refused(
    app_engine, performance_site
) -> None:
    """The one-active-run rule must survive the idempotency handling."""
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(performance_site["tenant_id"])},
        )
        service = PerformanceService(
            session,
            TenantContext(
                tenant_id=performance_site["tenant_id"],
                actor_id=performance_site["actor_id"],
                role=Role.OWNER,
                trace_id="integration",
            ),
        )
        await service.create_run(performance_site["site_id"], "perf-first-001")
        with pytest.raises(HTTPException) as error:
            await service.create_run(performance_site["site_id"], "perf-second-01")
        assert error.value.detail == "performance_run_already_active"
