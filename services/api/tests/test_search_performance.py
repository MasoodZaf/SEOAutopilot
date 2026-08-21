from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchPerformanceRead
from app.core.context import Role, TenantContext
from app.db.models import Site
from app.services.search_performance import SearchPerformanceService


def context() -> TenantContext:
    return TenantContext(tenant_id=uuid4(), actor_id=uuid4(), role=Role.VIEWER, trace_id="trace")


@pytest.mark.asyncio
async def test_summary_is_tenant_scoped_and_uses_weighted_position() -> None:
    tenant_context = context()
    site = Site(
        id=uuid4(),
        tenant_id=tenant_context.tenant_id,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
    )
    aggregate = SimpleNamespace(rows=15, clicks=2.0, impressions=16.0, weighted_position=180.0)
    execution = MagicMock()
    execution.one.return_value = aggregate
    mocked = MagicMock(spec=AsyncSession)
    mocked.scalar = AsyncMock(return_value=site)
    mocked.execute = AsyncMock(return_value=execution)

    summary = await SearchPerformanceService(cast(AsyncSession, mocked), tenant_context).summarize(
        site.id, date(2026, 7, 21), date(2026, 8, 17)
    )

    assert summary.clicks == 2.0
    assert summary.impressions == 16.0
    assert summary.ctr == 0.125
    assert summary.position == 11.25
    assert summary.is_sparse is True
    assert SearchPerformanceRead.model_validate(summary).position == 11.25
    statement = str(mocked.execute.await_args.args[0])
    assert "search_metric.tenant_id" in statement
    assert "search_metric.site_id" in statement
    assert "search_metric.query_hash" not in statement


@pytest.mark.asyncio
async def test_summary_hides_cross_tenant_site_as_not_found() -> None:
    mocked = MagicMock(spec=AsyncSession)
    mocked.scalar = AsyncMock(return_value=None)
    mocked.execute = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await SearchPerformanceService(cast(AsyncSession, mocked), context()).summarize(
            uuid4(), date(2026, 7, 21), date(2026, 8, 17)
        )

    assert error.value.status_code == 404
    mocked.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_rejects_ranges_over_ninety_days() -> None:
    mocked = MagicMock(spec=AsyncSession)
    with pytest.raises(HTTPException) as error:
        await SearchPerformanceService(cast(AsyncSession, mocked), context()).summarize(
            uuid4(), date(2026, 1, 1), date(2026, 4, 1)
        )
    assert error.value.status_code == 422
    assert error.value.detail == "invalid_range"
