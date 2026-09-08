import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.db.models import (
    AuditEvent,
    DeploymentReceipt,
    MeasurementSeries,
    OutboxEvent,
    PostDeployVerification,
    Proposal,
    SearchMetric,
)
from app.domain.measurement import (
    calculate_measurement_delta,
    changed_lines,
    verify_rendered_content,
)


def stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class MeasurementService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def verify_deployment(
        self,
        proposal_id: UUID,
        live_body: str | None = None,
        live_status: int = 200,
    ) -> PostDeployVerification:
        proposal = await self.session.scalar(
            select(Proposal).where(
                Proposal.id == proposal_id,
                Proposal.tenant_id == self.context.tenant_id,
            )
        )
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal_not_found")

        receipt = await self.session.scalar(
            select(DeploymentReceipt).where(
                DeploymentReceipt.proposal_id == proposal_id,
                DeploymentReceipt.tenant_id == self.context.tenant_id,
            )
        )
        if receipt is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="proposal_has_no_deployment_receipt",
            )

        if live_body is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="live_verification_evidence_required",
            )

        # The caller must supply independently fetched connector/crawler evidence.
        #
        # What is looked for is the lines this change *adds*, not the whole
        # file. `after_content` is the entire document, so requiring it verbatim
        # asked whether the served page is byte-identical to the source rather
        # than whether this change is live -- a question that answers "no" the
        # moment anything else in the file changes, and never distinguishes this
        # change from any other.
        added = changed_lines(proposal.diff_unified)
        expected_pattern = "\n".join(added) if added else proposal.after_content.strip()
        ver_result = verify_rendered_content(expected_pattern, live_body, live_status)
        now = datetime.now(UTC)
        ver_status = "verified" if ver_result.is_verified else "failed"

        verification = await self.session.scalar(
            select(PostDeployVerification).where(
                PostDeployVerification.deployment_receipt_id == receipt.id,
                PostDeployVerification.tenant_id == self.context.tenant_id,
            )
        )
        if verification is None:
            verification = PostDeployVerification(
                tenant_id=self.context.tenant_id,
                site_id=proposal.site_id,
                proposal_id=proposal.id,
                deployment_receipt_id=receipt.id,
                page_id=proposal.page_id,
                status=ver_status,
                verified_at=now if ver_result.is_verified else None,
                expected_pattern=expected_pattern[:500],
                observed_snippet=ver_result.observed_snippet[:500] if ver_result.observed_snippet else None,
                http_status=ver_result.http_status,
                notes=ver_result.notes,
            )
            self.session.add(verification)
        else:
            verification.status = ver_status
            verification.verified_at = now if ver_result.is_verified else None
            verification.observed_snippet = ver_result.observed_snippet[:500] if ver_result.observed_snippet else None
            verification.http_status = ver_result.http_status
            verification.notes = ver_result.notes

        if ver_result.is_verified:
            receipt.verified_at = now

        event_payload = {
            "proposal_id": str(proposal.id),
            "verification_status": ver_status,
            "http_status": ver_result.http_status,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="system",
                actor_id=str(self.context.actor_id),
                action=f"proposal.verification_{ver_status}",
                resource_type="proposal",
                resource_id=str(proposal.id),
                trace_id=self.context.trace_id,
                metadata_json=event_payload,
                event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        self.session.add(
            OutboxEvent(
                tenant_id=self.context.tenant_id,
                event_type=f"proposal.verification_{ver_status}.v1",
                event_version=1,
                aggregate_type="proposal",
                aggregate_id=proposal.id,
                payload=event_payload,
            )
        )
        await self.session.flush()
        await self.session.refresh(verification)
        return verification

    async def get_verification(self, proposal_id: UUID) -> PostDeployVerification | None:
        return await self.session.scalar(
            select(PostDeployVerification).where(
                PostDeployVerification.proposal_id == proposal_id,
                PostDeployVerification.tenant_id == self.context.tenant_id,
            )
        )

    async def calculate_or_get_measurement(self, proposal_id: UUID) -> MeasurementSeries:
        proposal = await self.session.scalar(
            select(Proposal).where(
                Proposal.id == proposal_id,
                Proposal.tenant_id == self.context.tenant_id,
            )
        )
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal_not_found")

        receipt = await self.session.scalar(
            select(DeploymentReceipt).where(
                DeploymentReceipt.proposal_id == proposal_id,
                DeploymentReceipt.tenant_id == self.context.tenant_id,
            )
        )
        if receipt is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="proposal_not_deployed",
            )

        deployed_at = receipt.deployed_at
        baseline_start = deployed_at - timedelta(days=28)
        baseline_end = deployed_at
        followup_start = deployed_at
        followup_end = deployed_at + timedelta(days=28)

        existing = await self.session.scalar(
            select(MeasurementSeries).where(
                MeasurementSeries.proposal_id == proposal_id,
                MeasurementSeries.tenant_id == self.context.tenant_id,
                MeasurementSeries.followup_window_end == followup_end,
            )
        )
        if existing is not None:
            return existing

        verification = await self.session.scalar(
            select(PostDeployVerification).where(
                PostDeployVerification.proposal_id == proposal_id,
                PostDeployVerification.tenant_id == self.context.tenant_id,
                PostDeployVerification.status == "verified",
            )
        )
        if verification is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="deployment_not_verified",
            )
        if datetime.now(UTC) < followup_end:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="measurement_window_incomplete",
            )

        # Query baseline metrics for the page
        baseline_rows = await self.session.scalars(
            select(SearchMetric).where(
                SearchMetric.tenant_id == self.context.tenant_id,
                SearchMetric.site_id == proposal.site_id,
                SearchMetric.page_id == proposal.page_id,
                SearchMetric.metric_date >= baseline_start.date(),
                SearchMetric.metric_date <= baseline_end.date(),
            )
        )
        b_list = list(baseline_rows)
        b_clicks = sum(m.clicks for m in b_list)
        b_impressions = sum(m.impressions for m in b_list)
        b_ctr = (b_clicks / b_impressions) if b_impressions > 0 else 0.0
        b_pos = (sum(m.position for m in b_list) / len(b_list)) if b_list else None

        baseline_metrics = {
            "clicks": b_clicks,
            "impressions": b_impressions,
            "ctr": b_ctr,
            "position": b_pos,
        }

        # Query follow-up metrics for the page
        followup_rows = await self.session.scalars(
            select(SearchMetric).where(
                SearchMetric.tenant_id == self.context.tenant_id,
                SearchMetric.site_id == proposal.site_id,
                SearchMetric.page_id == proposal.page_id,
                SearchMetric.metric_date >= followup_start.date(),
                SearchMetric.metric_date <= followup_end.date(),
            )
        )
        f_list = list(followup_rows)
        f_clicks = sum(m.clicks for m in f_list)
        f_impressions = sum(m.impressions for m in f_list)
        f_ctr = (f_clicks / f_impressions) if f_impressions > 0 else 0.0
        f_pos = (sum(m.position for m in f_list) / len(f_list)) if f_list else None

        followup_metrics = {
            "clicks": f_clicks,
            "impressions": f_impressions,
            "ctr": f_ctr,
            "position": f_pos,
        }

        delta_summary = calculate_measurement_delta(baseline_metrics, followup_metrics)

        series = MeasurementSeries(
            tenant_id=self.context.tenant_id,
            site_id=proposal.site_id,
            proposal_id=proposal.id,
            page_id=proposal.page_id,
            baseline_window_start=baseline_start,
            baseline_window_end=baseline_end,
            followup_window_start=followup_start,
            followup_window_end=followup_end,
            baseline_metrics=baseline_metrics,
            followup_metrics=followup_metrics,
            delta_metrics=delta_summary.to_dict(),
            confidence_score=delta_summary.confidence_score,
            is_sparse=delta_summary.is_sparse,
            annotations=delta_summary.caveats,
        )
        self.session.add(series)

        event_payload = {
            "proposal_id": str(proposal.id),
            "site_id": str(proposal.site_id),
            "clicks_delta": delta_summary.clicks_delta,
            "impressions_delta": delta_summary.impressions_delta,
            "confidence_score": delta_summary.confidence_score,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="system",
                actor_id=str(self.context.actor_id),
                action="measurement.calculated",
                resource_type="measurement_series",
                resource_id=str(series.id),
                trace_id=self.context.trace_id,
                metadata_json=event_payload,
                event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        self.session.add(
            OutboxEvent(
                tenant_id=self.context.tenant_id,
                event_type="measurement.calculated.v1",
                event_version=1,
                aggregate_type="measurement_series",
                aggregate_id=series.id,
                payload=event_payload,
            )
        )
        await self.session.flush()
        await self.session.refresh(series)
        return series

    async def list_site_measurements(self, site_id: UUID, limit: int = 50) -> list[MeasurementSeries]:
        result = await self.session.scalars(
            select(MeasurementSeries)
            .where(
                MeasurementSeries.site_id == site_id,
                MeasurementSeries.tenant_id == self.context.tenant_id,
            )
            .order_by(MeasurementSeries.calculated_at.desc())
            .limit(limit)
        )
        return list(result)
