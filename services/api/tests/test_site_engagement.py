from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import EngagementRead
from app.core.context import Role, TenantContext
from app.db.models import Site
from app.services.site_engagement import SiteEngagementService


def context() -> TenantContext:
    return TenantContext(tenant_id=uuid4(), actor_id=uuid4(), role=Role.VIEWER, trace_id="trace")


def session_returning(site: Site | None, totals: object, pages: list[object]) -> MagicMock:
    totals_result = MagicMock()
    totals_result.one.return_value = totals
    pages_result = MagicMock()
    pages_result.all.return_value = pages
    mocked = MagicMock(spec=AsyncSession)
    mocked.scalar = AsyncMock(return_value=site)
    mocked.execute = AsyncMock(side_effect=[totals_result, pages_result])
    return mocked


def site_for(tenant_context: TenantContext) -> Site:
    return Site(
        id=uuid4(),
        tenant_id=tenant_context.tenant_id,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
    )


@pytest.mark.asyncio
async def test_summary_is_tenant_scoped_and_ranks_landing_pages() -> None:
    tenant_context = context()
    site = site_for(tenant_context)
    totals = SimpleNamespace(rows=9, sessions=40.0, engaged_sessions=10.0, views=55.0, key_events=1.0)
    pages = [
        SimpleNamespace(landing_page="/", sessions=30.0, engaged_sessions=8.0),
        SimpleNamespace(landing_page="/pricing", sessions=10.0, engaged_sessions=2.0),
    ]
    mocked = session_returning(site, totals, pages)

    summary = await SiteEngagementService(cast(AsyncSession, mocked), tenant_context).summarize(
        site.id, date(2026, 8, 20), date(2026, 9, 17)
    )

    assert summary.sessions == 40.0
    assert summary.engagement_rate == 0.25
    assert [page.landing_page for page in summary.top_landing_pages] == ["/", "/pricing"]
    assert EngagementRead.model_validate(summary).top_landing_pages[1].sessions == 10.0
    for call in mocked.execute.await_args_list:
        statement, params = call.args
        assert "tenant_id = :tenant_id" in str(statement)
        assert params["tenant_id"] == tenant_context.tenant_id
        assert params["site_id"] == site.id


@pytest.mark.asyncio
async def test_no_sessions_has_no_rate_rather_than_zero() -> None:
    tenant_context = context()
    site = site_for(tenant_context)
    totals = SimpleNamespace(rows=0, sessions=0.0, engaged_sessions=0.0, views=0.0, key_events=0.0)
    mocked = session_returning(site, totals, [])

    summary = await SiteEngagementService(cast(AsyncSession, mocked), tenant_context).summarize(
        site.id, date(2026, 8, 20), date(2026, 9, 17)
    )

    assert summary.engagement_rate is None
    assert summary.top_landing_pages == []


@pytest.mark.asyncio
async def test_cross_tenant_site_is_not_found_and_reads_nothing() -> None:
    mocked = MagicMock(spec=AsyncSession)
    mocked.scalar = AsyncMock(return_value=None)
    mocked.execute = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await SiteEngagementService(cast(AsyncSession, mocked), context()).summarize(
            uuid4(), date(2026, 8, 20), date(2026, 9, 17)
        )

    assert error.value.status_code == 404
    mocked.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_ranges_over_ninety_days() -> None:
    mocked = MagicMock(spec=AsyncSession)
    with pytest.raises(HTTPException) as error:
        await SiteEngagementService(cast(AsyncSession, mocked), context()).summarize(
            uuid4(), date(2026, 1, 1), date(2026, 4, 1)
        )
    assert error.value.status_code == 422
