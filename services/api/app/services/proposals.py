import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import DeploymentCreate, ProposalApprovalCreate, ProposalCreate
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    DeploymentReceipt,
    Opportunity,
    OutboxEvent,
    Page,
    Proposal,
    ProposalApproval,
    Site,
)
from app.domain.deployments import (
    DeploymentAdapter,
    DeploymentManifest,
    DeploymentRequest,
    DriftDetectedError,
)
from app.domain.proposals import (
    compute_content_hash,
    evaluate_proposal_policy,
    generate_unified_diff,
    validate_proposal_content,
)

ALLOWED_PROPOSAL_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR, Role.DEVELOPER}
ALLOWED_APPROVAL_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER}
ALLOWED_DEPLOYMENT_ROLES = {Role.OWNER, Role.ADMIN, Role.DEVELOPER}


def stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class ProposalService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        deployments_enabled: bool = False,
    ) -> None:
        self.session = session
        self.context = context
        self.deployments_enabled = deployments_enabled

    async def create_proposal(self, site_id: UUID, command: ProposalCreate) -> Proposal:
        if self.context.role not in ALLOWED_PROPOSAL_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_to_create_proposal",
            )
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        opportunity = await self.session.scalar(
            select(Opportunity).where(
                Opportunity.id == command.opportunity_id,
                Opportunity.site_id == site_id,
                Opportunity.tenant_id == self.context.tenant_id,
            )
        )
        if opportunity is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="opportunity_not_found")

        page = await self.session.scalar(
            select(Page).where(
                Page.id == command.page_id,
                Page.site_id == site_id,
                Page.tenant_id == self.context.tenant_id,
            )
        )
        if page is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page_not_found")

        base_hash = compute_content_hash(command.before_content)
        diff_unified = generate_unified_diff(
            command.before_content, command.after_content, command.target_path
        )
        validations = validate_proposal_content(
            command.target_type, command.target_path, command.before_content, command.after_content
        )
        policy = evaluate_proposal_policy(
            command.target_type,
            command.target_path,
            command.before_content,
            command.after_content,
            validations,
            author_id=self.context.actor_id,
            tenant_mode=site.mode,
        )

        proposal_payload = {
            "title": command.title,
            "rationale": command.rationale,
            "target_type": command.target_type,
            "target_path": command.target_path,
            "before_content": command.before_content,
            "after_content": command.after_content,
        }
        proposal_hash = stable_hash(proposal_payload)
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=command.expires_in_days)

        if policy.risk == "prohibited":
            initial_status = "rejected"
        elif any(not v.passed for v in validations):
            initial_status = "draft"
        elif policy.required_approver_count > 1:
            initial_status = "review_required"
        else:
            initial_status = "validated"

        proposal = Proposal(
            tenant_id=self.context.tenant_id,
            site_id=site_id,
            opportunity_id=command.opportunity_id,
            page_id=command.page_id,
            author_id=self.context.actor_id,
            title=command.title,
            rationale=command.rationale,
            target_type=command.target_type,
            target_path=command.target_path,
            before_content=command.before_content,
            after_content=command.after_content,
            diff_unified=diff_unified,
            base_hash=base_hash,
            proposal_hash=proposal_hash,
            risk=policy.risk,
            status=initial_status,
            validations_json=[v.to_dict() for v in validations],
            policy_evaluation_json=policy.to_dict(),
            evidence_refs=opportunity.evidence_refs,
            expires_at=expires_at,
        )
        self.session.add(proposal)
        await self.session.flush()

        event_payload = {
            "proposal_id": str(proposal.id),
            "site_id": str(site_id),
            "opportunity_id": str(command.opportunity_id),
            "author_id": str(self.context.actor_id),
            "risk": policy.risk,
            "status": initial_status,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="proposal.created",
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
                event_type="proposal.created.v1",
                event_version=1,
                aggregate_type="proposal",
                aggregate_id=proposal.id,
                payload=event_payload,
            )
        )
        await self.session.commit()
        await self.session.refresh(proposal)
        return proposal

    async def list_proposals(
        self,
        site_id: UUID,
        status_filter: str | None = None,
        risk_filter: str | None = None,
        limit: int = 50,
    ) -> list[Proposal]:
        query = select(Proposal).where(
            Proposal.site_id == site_id,
            Proposal.tenant_id == self.context.tenant_id,
        )
        if status_filter:
            query = query.where(Proposal.status == status_filter)
        if risk_filter:
            query = query.where(Proposal.risk == risk_filter)
        query = query.order_by(Proposal.created_at.desc()).limit(limit)
        result = await self.session.scalars(query)
        return list(result)

    async def get_proposal(self, proposal_id: UUID) -> Proposal | None:
        return await self.session.scalar(
            select(Proposal).where(
                Proposal.id == proposal_id,
                Proposal.tenant_id == self.context.tenant_id,
            )
        )

    async def approve_proposal(
        self,
        proposal_id: UUID,
        command: ProposalApprovalCreate,
    ) -> ProposalApproval:
        proposal = await self.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal_not_found")

        now = datetime.now(UTC)
        if proposal.expires_at < now:
            proposal.status = "expired"
            await self.session.commit()
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="proposal_expired")

        if proposal.status in {"approved", "rejected", "deployed"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"proposal_already_{proposal.status}",
            )

        if proposal.risk == "prohibited":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="prohibited_proposal_cannot_be_approved",
            )

        # Separation of duties: author cannot approve their own proposal
        if self.context.actor_id == proposal.author_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="author_cannot_approve_own_proposal",
            )

        if self.context.role not in ALLOWED_APPROVAL_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_to_approve_proposal",
            )

        approval = ProposalApproval(
            tenant_id=self.context.tenant_id,
            proposal_id=proposal.id,
            proposal_version=proposal.version,
            approver_id=self.context.actor_id,
            decision=command.decision,
            notes=command.notes,
        )
        self.session.add(approval)

        if command.decision == "rejected":
            proposal.status = "rejected"
        else:
            # Check policy required approvers
            required_count = int(proposal.policy_evaluation_json.get("required_approver_count", 1))
            existing_approvals = await self.session.scalars(
                select(ProposalApproval).where(
                    ProposalApproval.proposal_id == proposal.id,
                    ProposalApproval.proposal_version == proposal.version,
                    ProposalApproval.decision == "approved",
                )
            )
            count = len(list(existing_approvals)) + 1
            if count >= required_count:
                proposal.status = "approved"
            else:
                proposal.status = "review_required"

        proposal.updated_at = now

        event_payload = {
            "proposal_id": str(proposal.id),
            "decision": command.decision,
            "approver_id": str(self.context.actor_id),
            "resulting_status": proposal.status,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action=f"proposal.{command.decision}",
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
                event_type=f"proposal.{command.decision}.v1",
                event_version=1,
                aggregate_type="proposal",
                aggregate_id=proposal.id,
                payload=event_payload,
            )
        )
        await self.session.commit()
        await self.session.refresh(approval)
        return approval

    async def deploy_proposal(
        self,
        proposal_id: UUID,
        command: DeploymentCreate,
        idempotency_key: str,
        adapter: DeploymentAdapter,
    ) -> DeploymentReceipt:
        if self.context.role not in ALLOWED_DEPLOYMENT_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_to_deploy_proposal",
            )
        if not self.deployments_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="deployments_disabled",
            )
        proposal = await self.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal_not_found")

        # Check existing receipt for idempotency
        existing_receipt = await self.session.scalar(
            select(DeploymentReceipt).where(
                DeploymentReceipt.tenant_id == self.context.tenant_id,
                DeploymentReceipt.idempotency_key == idempotency_key,
            )
        )
        if existing_receipt is not None:
            return existing_receipt

        now = datetime.now(UTC)
        if proposal.expires_at < now:
            proposal.status = "expired"
            await self.session.commit()
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="proposal_expired")

        if proposal.status != "approved":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="proposal_must_be_approved_before_deployment",
            )

        site = await self.session.scalar(
            select(Site).where(
                Site.id == proposal.site_id,
                Site.tenant_id == self.context.tenant_id,
            )
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        if site.mode not in {"recommend", "autopilot"}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="site_mode_blocks_deployment")
        if site.emergency_freeze:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="emergency_freeze_active")
        if (
            site.freeze_window_start is not None
            and site.freeze_window_end is not None
            and site.freeze_window_start <= now <= site.freeze_window_end
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="scheduled_freeze_window_active",
            )
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=UTC)
        deployment_count = await self.session.scalar(
            select(func.count())
            .select_from(DeploymentReceipt)
            .where(
                DeploymentReceipt.tenant_id == self.context.tenant_id,
                DeploymentReceipt.site_id == proposal.site_id,
                DeploymentReceipt.deployed_at >= start_of_day,
            )
        )
        if int(deployment_count or 0) >= site.daily_change_budget:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="daily_change_budget_exhausted",
            )

        # Gather approved approver IDs
        approvers = await self.session.scalars(
            select(ProposalApproval.approver_id).where(
                ProposalApproval.proposal_id == proposal.id,
                ProposalApproval.proposal_version == proposal.version,
                ProposalApproval.decision == "approved",
            )
        )
        approver_ids = [str(a) for a in approvers]

        manifest = DeploymentManifest(
            tenant_id=str(self.context.tenant_id),
            site_id=str(proposal.site_id),
            proposal_id=str(proposal.id),
            target_path=proposal.target_path,
            base_hash=proposal.base_hash,
            proposal_hash=proposal.proposal_hash,
            author_id=str(proposal.author_id),
            approver_ids=approver_ids,
            deployed_at=now.isoformat(),
            version=proposal.version,
        )

        request = DeploymentRequest(
            tenant_id=self.context.tenant_id,
            site_id=proposal.site_id,
            proposal_id=proposal.id,
            target_type=proposal.target_type,
            target_path=proposal.target_path,
            diff_unified=proposal.diff_unified,
            base_hash=proposal.base_hash,
            after_content=proposal.after_content,
            idempotency_key=idempotency_key,
            manifest=manifest,
            current_live_content=command.current_live_content,
        )

        try:
            result = await adapter.deploy(request)
        except DriftDetectedError as drift_err:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(drift_err),
            ) from drift_err

        receipt = DeploymentReceipt(
            tenant_id=self.context.tenant_id,
            site_id=proposal.site_id,
            proposal_id=proposal.id,
            connector_type=result.connector_type,
            idempotency_key=idempotency_key,
            external_ref=result.external_ref,
            manifest_json=result.manifest_json,
            status=result.status,
            deployed_at=now,
        )
        self.session.add(receipt)
        proposal.status = "deployed"
        proposal.updated_at = now

        event_payload = {
            "deployment_id": str(receipt.id),
            "proposal_id": str(proposal.id),
            "site_id": str(proposal.site_id),
            "external_ref": result.external_ref,
            "connector_type": result.connector_type,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="proposal.deployed",
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
                event_type="proposal.deployed.v1",
                event_version=1,
                aggregate_type="proposal",
                aggregate_id=proposal.id,
                payload=event_payload,
            )
        )
        await self.session.commit()
        await self.session.refresh(receipt)
        return receipt
