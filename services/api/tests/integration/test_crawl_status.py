"""The crawl status panel's data, against PostgreSQL with RLS on.

The panel shows a live bar from `progress` and `last_heartbeat_at`, and the
last few outcomes. Pinned here: the crawler's snapshot comes back as written,
history is newest first and bounded, and one workspace cannot read another's.
"""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.core.context import Role, TenantContext
from app.services.sites import SiteService
from tests.conftest import requires_database
from tests.integration import test_github_connector as github
from tests.integration.test_github_connector import scoped

pytestmark = [pytest.mark.asyncio, requires_database]

acme = github.acme
other = github.other


def sites(session, ids, tenant=None) -> SiteService:
    return SiteService(
        session,
        TenantContext(
            tenant_id=tenant or ids["tenant_id"],
            actor_id=ids["actor_id"],
            role=Role.OWNER,
            trace_id="integration",
        ),
    )


async def seed(session, ids, status: str, minutes_ago: int, **extra) -> None:
    await session.execute(
        text(
            "INSERT INTO crawl_job(id,tenant_id,site_id,kind,status,requested_by,config_snapshot,"
            "created_at,progress,error_code,last_heartbeat_at)"
            " VALUES(:id,:t,:s,'full',:status,:actor,'{}'::jsonb,"
            " now()-make_interval(mins=>:ago),CAST(:progress AS jsonb),:error,now())"
        ),
        {
            "id": uuid4(),
            "t": ids["tenant_id"],
            "s": ids["site_id"],
            "status": status,
            "actor": ids["actor_id"],
            "ago": minutes_ago,
            "progress": extra.get("progress", "{}"),
            "error": extra.get("error"),
        },
    )


async def test_history_is_newest_first_and_carries_live_progress(app_engine, acme) -> None:
    async with scoped(app_engine, acme["tenant_id"]) as session:
        await seed(session, acme, "failed", 60 * 24, error="content_collapse")
        await seed(session, acme, "completed", 60)
        await seed(
            session,
            acme,
            "running",
            2,
            progress='{"phase":"fetching","fetched":120,"pending":300,"max_pages":500}',
        )
        crawls = await sites(session, acme).recent_crawls(acme["site_id"], 5)
        latest = await sites(session, acme).recent_crawls(acme["site_id"], 1)

        assert [item.status for item in crawls] == ["running", "completed", "failed"]
        assert crawls[0].progress["fetched"] == 120
        assert crawls[0].last_heartbeat_at is not None
        assert crawls[2].error_code == "content_collapse"
        assert len(latest) == 1


async def test_another_workspace_cannot_read_the_history(app_engine, acme, other) -> None:
    async with scoped(app_engine, other["tenant_id"]) as session:
        with pytest.raises(HTTPException) as refused:
            await sites(session, other).recent_crawls(acme["site_id"], 5)
    assert refused.value.detail == "site_not_found"
