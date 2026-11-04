"""An unverified site gets one small crawl a day, and a workspace five, under RLS."""

from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.schemas import CrawlCreate
from app.core.context import Role, TenantContext
from app.services.sites import PREVIEW_MAX_PAGES, SiteService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


@pytest_asyncio.fixture
async def workspace(engine):
    tenant, actor = uuid4(), uuid4()
    sites = [uuid4() for _ in range(6)]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant(id,slug,name,status) VALUES(:t,:slug,'w','active')"),
            {"t": tenant, "slug": f"p-{tenant.hex[:8]}"},
        )
        for index, site in enumerate(sites):
            await session.execute(
                text("INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status)"
                     " VALUES(:s,:t,'x',:o,:h,'observe','pending_verification')"),
                {"s": site, "t": tenant, "o": f"https://s{index}.example", "h": f"s{index}.example"},
            )
    yield tenant, actor, sites
    async with factory() as session, session.begin():
        for table in ("outbox_event", "audit_event", "crawl_job", "site"):
            await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})
        await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": tenant})


async def test_preview_crawls_are_capped_per_site_and_per_workspace(tenant_session_factory, workspace):
    tenant, actor, sites = workspace
    context = TenantContext(tenant_id=tenant, actor_id=actor, role=Role.OWNER, trace_id="t")
    async with tenant_session_factory(tenant) as session:
        service = SiteService(session, context)
        first = await service.create_crawl(sites[0], CrawlCreate(max_pages=500, max_depth=10))
        assert (first.config_snapshot["max_pages"], first.config_snapshot["preview"]) == (PREVIEW_MAX_PAGES, True)
        await session.execute(text("UPDATE crawl_job SET status='completed' WHERE id=:id"), {"id": first.id})

        with pytest.raises(HTTPException) as caught:
            await service.create_crawl(sites[0], CrawlCreate())
        assert caught.value.detail == "preview_crawl_daily_limit"

        for site in sites[1:5]:
            await service.create_crawl(site, CrawlCreate())
        with pytest.raises(HTTPException) as caught:
            await service.create_crawl(sites[5], CrawlCreate())
        assert caught.value.detail == "preview_crawl_workspace_limit"
        await session.commit()
