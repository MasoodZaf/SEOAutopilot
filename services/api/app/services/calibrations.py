from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CalibrationReviewCreate
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    CalibrationItem,
    CalibrationReview,
    CalibrationRun,
    CrawlJob,
    Finding,
    OpportunityFinding,
    OutboxEvent,
    Page,
    PageObservation,
    Site,
)
from app.services.opportunities import build_diverse_top_query


def stable_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def review_request_hash(item_id: UUID, command: CalibrationReviewCreate) -> str:
    return stable_hash(
        {
            "item_id": str(item_id),
            "accuracy_label": command.accuracy_label,
            "actionability": command.actionability,
            "severity_fit": command.severity_fit,
            "notes": command.notes,
        }
    )


def calibration_summary(
    *, target_size: int, reviews: Sequence[CalibrationReview]
) -> dict[str, int | float | None]:
    true_positive = sum(review.accuracy_label == "true_positive" for review in reviews)
    false_positive = sum(review.accuracy_label == "false_positive" for review in reviews)
    uncertain = sum(review.accuracy_label == "uncertain" for review in reviews)
    decided = true_positive + false_positive
    return {
        "target_size": target_size,
        "reviewed": len(reviews),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "uncertain": uncertain,
        "precision": round(true_positive / decided, 4) if decided else None,
        "actionable": sum(review.actionability in {"accept", "edit"} for review in reviews),
    }


class CalibrationService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def create_run(
        self, site_id: UUID, *, target_size: int, idempotency_key: str
    ) -> dict[str, object]:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER)
        site = await self.session.scalar(
            select(Site).where(
                Site.id == site_id,
                Site.tenant_id == self.context.tenant_id,
                Site.status == "active",
            )
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        latest_crawl = await self.session.scalar(
            select(CrawlJob)
            .where(
                CrawlJob.tenant_id == self.context.tenant_id,
                CrawlJob.site_id == site_id,
            )
            .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
            .limit(1)
        )
        if latest_crawl is None or latest_crawl.status not in {"completed", "partial"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="calibration_evidence_not_ready",
            )
        request_hash = stable_hash(
            {"site_id": str(site_id), "target_size": target_size, "strategy": "top_opportunities_v1"}
        )
        existing = await self.session.scalar(
            select(CalibrationRun).where(
                CalibrationRun.tenant_id == self.context.tenant_id,
                CalibrationRun.site_id == site_id,
                CalibrationRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="idempotency_key_reused",
                )
            return await self._serialize_run(existing)
        open_run = await self.session.scalar(
            select(CalibrationRun.id).where(
                CalibrationRun.tenant_id == self.context.tenant_id,
                CalibrationRun.site_id == site_id,
                CalibrationRun.status == "open",
            )
        )
        if open_run is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="calibration_run_already_open",
            )

        opportunities = list(
            await self.session.scalars(
                build_diverse_top_query(
                    tenant_id=self.context.tenant_id,
                    site_id=site_id,
                    opportunity_status="open",
                    limit=target_size,
                )
            )
        )
        if not opportunities:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no_opportunities")
        if any(
            str(opportunity.evidence_refs.get("crawl_id")) != str(latest_crawl.id)
            for opportunity in opportunities
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="calibration_evidence_not_ready",
            )

        opportunity_ids = [item.id for item in opportunities]
        pages = {
            page.id: page
            for page in await self.session.scalars(
                select(Page).where(
                    Page.tenant_id == self.context.tenant_id,
                    Page.site_id == site_id,
                    Page.id.in_([item.page_id for item in opportunities]),
                )
            )
        }
        try:
            observation_ids = {
                opportunity.id: UUID(str(opportunity.evidence_refs["observation_id"]))
                for opportunity in opportunities
            }
        except (KeyError, ValueError) as error:
            raise RuntimeError("calibration_evidence_incomplete") from error
        observations = {
            observation.id: observation
            for observation in await self.session.scalars(
                select(PageObservation).where(
                    PageObservation.tenant_id == self.context.tenant_id,
                    PageObservation.id.in_(list(observation_ids.values())),
                )
            )
        }
        finding_rows = await self.session.execute(
            select(OpportunityFinding.opportunity_id, Finding)
            .join(
                Finding,
                (Finding.id == OpportunityFinding.finding_id)
                & (Finding.tenant_id == OpportunityFinding.tenant_id),
            )
            .where(
                OpportunityFinding.tenant_id == self.context.tenant_id,
                OpportunityFinding.opportunity_id.in_(opportunity_ids),
                Finding.tenant_id == self.context.tenant_id,
                Finding.site_id == site_id,
            )
        )
        findings = {opportunity_id: finding for opportunity_id, finding in finding_rows.all()}

        run = CalibrationRun(
            tenant_id=self.context.tenant_id,
            site_id=site_id,
            target_size=len(opportunities),
            strategy="top_opportunities_v1",
            scoring_version_id=opportunities[0].scoring_version_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_by=self.context.actor_id,
        )
        # As above, the lookup only serialises retries that do not overlap.
        # Two that do would both reach here, and the loser would surface the
        # unique violation as a 500 rather than as the run it already made.
        try:
            async with self.session.begin_nested():
                self.session.add(run)
                await self.session.flush()
        except IntegrityError:
            concurrent = await self.session.scalar(
                select(CalibrationRun).where(
                    CalibrationRun.tenant_id == self.context.tenant_id,
                    CalibrationRun.site_id == site_id,
                    CalibrationRun.idempotency_key == idempotency_key,
                )
            )
            if concurrent is None:
                raise
            return await self._serialize_run(concurrent)
        items: list[CalibrationItem] = []
        for ordinal, opportunity in enumerate(opportunities, start=1):
            page = pages.get(opportunity.page_id)
            finding = findings.get(opportunity.id)
            observation = observations.get(observation_ids[opportunity.id])
            if page is None or finding is None or observation is None:
                raise RuntimeError("calibration_evidence_incomplete")
            snapshot = {
                "opportunity": {
                    "id": str(opportunity.id),
                    "title": opportunity.title,
                    "score": opportunity.score,
                    "confidence": opportunity.confidence,
                    "risk": opportunity.risk,
                    "scoring_version_id": str(opportunity.scoring_version_id),
                    "evidence_refs": opportunity.evidence_refs,
                },
                "page": {"id": str(page.id), "url": page.normalized_url},
                "finding": {
                    "id": str(finding.id),
                    "rule_key": finding.rule_key,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                },
                "observation": {
                    "id": str(observation.id),
                    "observed_at": observation.observed_at.isoformat(),
                    "http_status": observation.http_status,
                    "final_url": observation.final_url,
                    "title": observation.title,
                    "meta_description": observation.meta_description,
                    "h1": observation.h1_json,
                    "word_count": observation.word_count,
                    "rendered": observation.rendered,
                    "canonical_url": observation.canonical_url,
                    "robots_directives": observation.robots_directives,
                },
            }
            items.append(
                CalibrationItem(
                    tenant_id=self.context.tenant_id,
                    calibration_run_id=run.id,
                    opportunity_id=opportunity.id,
                    page_id=opportunity.page_id,
                    ordinal=ordinal,
                    rule_key=finding.rule_key,
                    evidence_snapshot=snapshot,
                )
            )
        self.session.add_all(items)
        event_payload = {"site_id": str(site_id), "target_size": len(items), "strategy": run.strategy}
        self.session.add_all(
            [
                AuditEvent(
                    tenant_id=self.context.tenant_id,
                    actor_type="user",
                    actor_id=str(self.context.actor_id),
                    action="calibration.run_created",
                    resource_type="calibration_run",
                    resource_id=str(run.id),
                    trace_id=self.context.trace_id,
                    metadata_json=event_payload,
                    event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
                ),
                OutboxEvent(
                    tenant_id=self.context.tenant_id,
                    event_type="calibration.run_created",
                    event_version=1,
                    aggregate_type="calibration_run",
                    aggregate_id=run.id,
                    payload=event_payload,
                ),
            ]
        )
        await self.session.flush()
        return await self._serialize_run(run)

    async def current_run(self, site_id: UUID) -> dict[str, object] | None:
        site = await self.session.scalar(
            select(Site.id).where(
                Site.id == site_id,
                Site.tenant_id == self.context.tenant_id,
            )
        )
        if site is None:
            return None
        run = await self.session.scalar(
            select(CalibrationRun)
            .where(
                CalibrationRun.tenant_id == self.context.tenant_id,
                CalibrationRun.site_id == site_id,
                CalibrationRun.status == "open",
            )
            .order_by(CalibrationRun.created_at.desc(), CalibrationRun.id.desc())
            .limit(1)
        )
        return await self._serialize_run(run) if run else None

    async def get_item(self, item_id: UUID) -> dict[str, object] | None:
        item = await self.session.scalar(
            select(CalibrationItem).where(
                CalibrationItem.id == item_id,
                CalibrationItem.tenant_id == self.context.tenant_id,
            )
        )
        if item is None:
            return None
        review = await self._current_review(item.id)
        return self._serialize_item(item, review)

    async def review_item(
        self,
        item_id: UUID,
        command: CalibrationReviewCreate,
        idempotency_key: str,
    ) -> CalibrationReview:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR)
        item = await self.session.scalar(
            select(CalibrationItem).where(
                CalibrationItem.id == item_id,
                CalibrationItem.tenant_id == self.context.tenant_id,
            )
        )
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="calibration_item_not_found")
        request_hash = review_request_hash(item_id, command)
        existing = await self.session.scalar(
            select(CalibrationReview).where(
                CalibrationReview.tenant_id == self.context.tenant_id,
                CalibrationReview.reviewer_id == self.context.actor_id,
                CalibrationReview.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="idempotency_key_reused",
                )
            return existing
        review = CalibrationReview(
            tenant_id=self.context.tenant_id,
            calibration_item_id=item.id,
            reviewer_id=self.context.actor_id,
            accuracy_label=command.accuracy_label,
            actionability=command.actionability,
            severity_fit=command.severity_fit,
            notes=command.notes,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(review)
                await self.session.flush()
        except IntegrityError:
            concurrent = await self.session.scalar(
                select(CalibrationReview).where(
                    CalibrationReview.tenant_id == self.context.tenant_id,
                    CalibrationReview.reviewer_id == self.context.actor_id,
                    CalibrationReview.idempotency_key == idempotency_key,
                )
            )
            if concurrent is None:
                raise
            return concurrent
        event_payload = {
            "calibration_item_id": str(item.id),
            "accuracy_label": review.accuracy_label,
            "actionability": review.actionability,
            "severity_fit": review.severity_fit,
        }
        self.session.add_all(
            [
                AuditEvent(
                    tenant_id=self.context.tenant_id,
                    actor_type="user",
                    actor_id=str(self.context.actor_id),
                    action="calibration.item_reviewed",
                    resource_type="calibration_item",
                    resource_id=str(item.id),
                    trace_id=self.context.trace_id,
                    metadata_json=event_payload,
                    event_hash=stable_hash({**event_payload, "review_id": str(review.id)}),
                ),
                OutboxEvent(
                    tenant_id=self.context.tenant_id,
                    event_type="calibration.item_reviewed",
                    event_version=1,
                    aggregate_type="calibration_item",
                    aggregate_id=item.id,
                    payload=event_payload,
                ),
            ]
        )
        return review

    async def _serialize_run(self, run: CalibrationRun) -> dict[str, object]:
        items = list(
            await self.session.scalars(
                select(CalibrationItem)
                .where(
                    CalibrationItem.tenant_id == self.context.tenant_id,
                    CalibrationItem.calibration_run_id == run.id,
                )
                .order_by(CalibrationItem.ordinal, CalibrationItem.id)
            )
        )
        reviews: list[CalibrationReview] = []
        serialized_items: list[dict[str, object]] = []
        for item in items:
            review = await self._current_review(item.id)
            if review:
                reviews.append(review)
            serialized_items.append(self._serialize_item(item, review))
        return {
            "id": run.id,
            "site_id": run.site_id,
            "status": run.status,
            "strategy": run.strategy,
            "target_size": run.target_size,
            "scoring_version_id": run.scoring_version_id,
            "created_at": run.created_at,
            "items": serialized_items,
            "summary": calibration_summary(target_size=run.target_size, reviews=reviews),
        }

    async def _current_review(self, item_id: UUID) -> CalibrationReview | None:
        return await self.session.scalar(
            select(CalibrationReview)
            .where(
                CalibrationReview.tenant_id == self.context.tenant_id,
                CalibrationReview.calibration_item_id == item_id,
                CalibrationReview.reviewer_id == self.context.actor_id,
            )
            .order_by(CalibrationReview.created_at.desc(), CalibrationReview.id.desc())
            .limit(1)
        )

    @staticmethod
    def _serialize_item(
        item: CalibrationItem, review: CalibrationReview | None
    ) -> dict[str, object]:
        return {
            "id": item.id,
            "calibration_run_id": item.calibration_run_id,
            "opportunity_id": item.opportunity_id,
            "page_id": item.page_id,
            "ordinal": item.ordinal,
            "rule_key": item.rule_key,
            "evidence_snapshot": item.evidence_snapshot,
            "current_review": review,
        }
