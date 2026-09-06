import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from statistics import median
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    CrawlJob,
    OutboxEvent,
    Page,
    PageObservation,
    PerformanceObservation,
    PerformanceRun,
    Site,
)


def request_hash(payload: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PerformanceService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def create_run(self, site_id: UUID, idempotency_key: str) -> PerformanceRun:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.DEVELOPER)
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=404, detail="site_not_found")
        if site.status != "active" or site.verified_at is None:
            raise HTTPException(status_code=409, detail="site_not_verified")
        payload = {"site_id": str(site.id), "strategy": "mobile", "source": "pagespeed_insights"}
        digest = request_hash(payload)
        prior = await self.session.scalar(
            select(PerformanceRun).where(
                PerformanceRun.tenant_id == self.context.tenant_id,
                PerformanceRun.site_id == site.id,
                PerformanceRun.idempotency_key == idempotency_key,
            )
        )
        if prior is not None:
            if prior.request_hash != digest:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            return prior
        active = await self.session.scalar(
            select(PerformanceRun).where(
                PerformanceRun.tenant_id == self.context.tenant_id,
                PerformanceRun.site_id == site.id,
                PerformanceRun.status.in_(("queued", "running")),
            )
        )
        if active is not None:
            raise HTTPException(status_code=409, detail="performance_run_already_active")
        crawl = await self.session.scalar(
            select(CrawlJob).where(
                CrawlJob.tenant_id == self.context.tenant_id,
                CrawlJob.site_id == site.id,
                CrawlJob.status.in_(("completed", "partial")),
            ).order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc()).limit(1)
        )
        if crawl is None:
            raise HTTPException(status_code=409, detail="performance_evidence_not_ready")
        target = await self.session.execute(
            select(Page, PageObservation.final_url)
            .join(PageObservation, PageObservation.page_id == Page.id)
            .where(
                Page.tenant_id == self.context.tenant_id,
                Page.site_id == site.id,
                PageObservation.tenant_id == self.context.tenant_id,
                PageObservation.crawl_job_id == crawl.id,
                PageObservation.http_status == 200,
            )
            .order_by(
                case((Page.normalized_url.in_((site.canonical_origin, site.canonical_origin + "/")), 0), else_=1),
                Page.normalized_url,
            ).limit(1)
        )
        row = target.first()
        if row is None:
            raise HTTPException(status_code=409, detail="performance_evidence_not_ready")
        page, target_url = row
        run = PerformanceRun(
            tenant_id=self.context.tenant_id,
            site_id=site.id,
            page_id=page.id,
            crawl_job_id=crawl.id,
            status="queued",
            strategy="mobile",
            source="pagespeed_insights",
            target_url=target_url,
            idempotency_key=idempotency_key,
            request_hash=digest,
            requested_by=self.context.actor_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(run)
                await self.session.flush()
        except IntegrityError as error:
            # Two constraints can fire here and they mean opposite things. The
            # one-active index means another run really is in flight. The
            # idempotency key means this caller is retrying their own request,
            # concurrently with their first attempt, and is owed its result --
            # and a retry that races itself trips the one-active index first,
            # so the constraint name is not what distinguishes the two. Whether
            # a run under this key exists is.
            concurrent = await self.session.scalar(
                select(PerformanceRun).where(
                    PerformanceRun.tenant_id == self.context.tenant_id,
                    PerformanceRun.site_id == site.id,
                    PerformanceRun.idempotency_key == idempotency_key,
                )
            )
            if concurrent is not None:
                return concurrent
            raise HTTPException(status_code=409, detail="performance_run_already_active") from error
        event_payload = {"performance_run_id": str(run.id), "site_id": str(site.id)}
        event_digest = request_hash({**event_payload, "actor_id": str(self.context.actor_id)})
        self.session.add_all([
            AuditEvent(tenant_id=self.context.tenant_id, actor_type="user", actor_id=str(self.context.actor_id), action="performance.requested", resource_type="performance_run", resource_id=str(run.id), trace_id=self.context.trace_id, metadata_json={"site_id": str(site.id), "strategy": "mobile"}, event_hash=event_digest),
            OutboxEvent(tenant_id=self.context.tenant_id, event_type="performance.requested", event_version=1, aggregate_type="performance_run", aggregate_id=run.id, payload=event_payload),
        ])
        return run

    async def latest(self, site_id: UUID) -> tuple[PerformanceRun, PerformanceObservation | None] | None:
        site = await self.session.scalar(select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id))
        if site is None:
            raise HTTPException(status_code=404, detail="site_not_found")
        result = await self.session.execute(
            select(PerformanceRun, PerformanceObservation)
            .outerjoin(PerformanceObservation, PerformanceObservation.performance_run_id == PerformanceRun.id)
            .where(PerformanceRun.tenant_id == self.context.tenant_id, PerformanceRun.site_id == site.id)
            .order_by(PerformanceRun.created_at.desc(), PerformanceRun.id.desc()).limit(1)
        )
        row = result.first()
        return (row[0], row[1]) if row is not None else None

    async def summary(self, site_id: UUID) -> dict[str, object] | None:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=404, detail="site_not_found")
        latest_result = await self.session.execute(
            select(PerformanceRun, PerformanceObservation)
            .join(PerformanceObservation, PerformanceObservation.performance_run_id == PerformanceRun.id)
            .where(
                PerformanceRun.tenant_id == self.context.tenant_id,
                PerformanceRun.site_id == site.id,
                PerformanceRun.status == "completed",
                PerformanceObservation.tenant_id == self.context.tenant_id,
                PerformanceObservation.site_id == site.id,
            )
            .order_by(PerformanceObservation.observed_at.desc(), PerformanceObservation.id.desc())
            .limit(1)
        )
        latest = latest_result.first()
        if latest is None:
            return None
        latest_run, latest_observation = latest
        sample_result = await self.session.execute(
            select(PerformanceObservation)
            .where(
                PerformanceObservation.tenant_id == self.context.tenant_id,
                PerformanceObservation.site_id == site.id,
                PerformanceObservation.page_id == latest_observation.page_id,
                PerformanceObservation.strategy == latest_observation.strategy,
                PerformanceObservation.source == latest_observation.source,
                PerformanceObservation.observed_at >= datetime.now(UTC) - timedelta(days=30),
            )
            .order_by(PerformanceObservation.observed_at.desc(), PerformanceObservation.id.desc())
            .limit(10)
        )
        observations = list(sample_result.scalars().all())
        return build_performance_summary(latest_run.target_url, observations)


def _median(values: Sequence[float | int | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return float(median(present)) if present else None


def build_performance_summary(
    target_url: str, observations: Sequence[PerformanceObservation]
) -> dict[str, object]:
    ordered = sorted(observations, key=lambda item: item.observed_at)
    if not ordered:
        raise ValueError("performance_summary_requires_observations")
    sample_count = len(ordered)
    return {
        "page_id": ordered[-1].page_id,
        "target_url": target_url,
        "strategy": ordered[-1].strategy,
        "source": ordered[-1].source,
        "sample_count": sample_count,
        "required_sample_count": 3,
        "status": "ready" if sample_count >= 3 else "insufficient_samples",
        "first_observed_at": ordered[0].observed_at,
        "last_observed_at": ordered[-1].observed_at,
        "median_performance_score": _median([item.performance_score for item in ordered]),
        "median_lcp_ms": _median([item.lcp_ms for item in ordered]),
        "median_inp_ms": _median([item.inp_ms for item in ordered]),
        "median_cls": _median([item.cls for item in ordered]),
        "median_ttfb_ms": _median([item.ttfb_ms for item in ordered]),
    }
