from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    CrawlJob,
    OutboxEvent,
    Page,
    PerformanceObservation,
    PerformanceRun,
    Site,
)
from app.services.performance import PerformanceService, build_performance_summary


def context(role: Role = Role.DEVELOPER) -> TenantContext:
    return TenantContext(tenant_id=uuid4(), actor_id=uuid4(), role=role, trace_id="trace")


@pytest.mark.asyncio
async def test_create_run_selects_server_side_crawl_page_and_stages_event() -> None:
    tenant_context = context()
    site = Site(id=uuid4(), tenant_id=tenant_context.tenant_id, name="Example", canonical_origin="https://example.com", normalized_host="example.com", status="active", verified_at=datetime.now(UTC))
    crawl = CrawlJob(id=uuid4(), tenant_id=tenant_context.tenant_id, site_id=site.id, status="completed", requested_by=tenant_context.actor_id, config_snapshot={})
    page = Page(id=uuid4(), tenant_id=tenant_context.tenant_id, site_id=site.id, normalized_url="https://example.com/", url_hash="a" * 64)
    target_result = MagicMock()
    target_result.first.return_value = (page, "https://example.com/")
    mocked = MagicMock(spec=AsyncSession)
    mocked.scalar = AsyncMock(side_effect=[site, None, None, crawl])
    mocked.execute = AsyncMock(return_value=target_result)
    mocked.flush = AsyncMock(side_effect=lambda: setattr(cast(PerformanceRun, mocked.add.call_args.args[0]), "id", uuid4()))
    nested = MagicMock()
    nested.__aenter__ = AsyncMock()
    nested.__aexit__ = AsyncMock(return_value=False)
    mocked.begin_nested.return_value = nested

    run = await PerformanceService(cast(AsyncSession, mocked), tenant_context).create_run(site.id, "performance-request-1")

    assert run.target_url == "https://example.com/"
    assert run.strategy == "mobile"
    staged = mocked.add_all.call_args.args[0]
    assert isinstance(staged[0], AuditEvent)
    assert isinstance(staged[1], OutboxEvent)
    assert staged[1].event_type == "performance.requested"
    statement = str(mocked.execute.await_args.args[0])
    assert "page.tenant_id" in statement
    assert "page_observation.crawl_job_id" in statement


@pytest.mark.asyncio
async def test_cross_tenant_site_is_hidden() -> None:
    mocked = MagicMock(spec=AsyncSession)
    mocked.scalar = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as error:
        await PerformanceService(cast(AsyncSession, mocked), context()).create_run(uuid4(), "performance-request-2")
    assert error.value.status_code == 404
    mocked.execute.assert_not_called()


@pytest.mark.asyncio
async def test_idempotent_replay_returns_existing_run() -> None:
    tenant_context = context()
    site = Site(id=uuid4(), tenant_id=tenant_context.tenant_id, name="Example", canonical_origin="https://example.com", normalized_host="example.com", status="active", verified_at=datetime.now(UTC))
    from app.services.performance import request_hash
    digest = request_hash({"site_id": str(site.id), "strategy": "mobile", "source": "pagespeed_insights"})
    run = PerformanceRun(id=uuid4(), tenant_id=tenant_context.tenant_id, site_id=site.id, page_id=uuid4(), crawl_job_id=uuid4(), status="completed", target_url="https://example.com/", strategy="mobile", source="pagespeed_insights", idempotency_key="performance-request-3", request_hash=digest, requested_by=tenant_context.actor_id)
    mocked = MagicMock(spec=AsyncSession)
    mocked.scalar = AsyncMock(side_effect=[site, run])
    result = await PerformanceService(cast(AsyncSession, mocked), tenant_context).create_run(site.id, "performance-request-3")
    assert result is run
    mocked.add.assert_not_called()


PAGE_ID = uuid4()


def observation(score: int, lcp: float | None, inp: float | None, cls: float | None, minute: int) -> PerformanceObservation:
    return PerformanceObservation(
        id=uuid4(), tenant_id=uuid4(), performance_run_id=uuid4(), site_id=uuid4(), page_id=PAGE_ID,
        observed_at=datetime(2026, 8, 20, 12, minute, tzinfo=UTC), strategy="mobile",
        source="pagespeed_insights", lighthouse_version="13.4.1", performance_score=score,
        lcp_ms=lcp, inp_ms=inp, cls=cls, ttfb_ms=2,
    )


def test_summary_requires_three_comparable_samples_before_ready() -> None:
    summary = build_performance_summary("https://example.com/", [
        observation(42, 11_058, None, 0.017, 1),
        observation(58, 7_000, None, 0.011, 2),
    ])
    assert summary["status"] == "insufficient_samples"
    assert summary["sample_count"] == 2
    assert summary["median_performance_score"] == 50
    assert summary["median_inp_ms"] is None


def test_summary_uses_medians_and_ignores_missing_metric_values() -> None:
    summary = build_performance_summary("https://example.com/", [
        observation(42, 11_058, None, 0.017, 1),
        observation(80, 4_000, 210, 0.009, 2),
        observation(58, 7_000, 190, 0.011, 3),
    ])
    assert summary["status"] == "ready"
    assert summary["median_performance_score"] == 58
    assert summary["median_lcp_ms"] == 7_000
    assert summary["median_inp_ms"] == 200
    assert summary["median_cls"] == 0.011
