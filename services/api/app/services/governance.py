import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    GovernanceSettingsUpdate,
    GovernanceStatusRead,
    SiteModeUpdate,
)
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    DeploymentReceipt,
    OutboxEvent,
    PolicySimulationRun,
    Proposal,
    RollbackReceipt,
    Site,
)
from app.domain.deployments import RollbackAdapter, RollbackRequest
from app.domain.governance import (
    simulate_policy_on_proposals,
)

ADMIN_ROLES = {Role.OWNER, Role.ADMIN}

# Ordered by how much the platform may do to the site without being asked again.
MODE_RANK = {"observe": 0, "recommend": 1, "autopilot": 2}
FREEZE_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER}


def stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class GovernanceService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def get_governance_status(self, site_id: UUID) -> GovernanceStatusRead:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        now = datetime.now(UTC)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=UTC)

        count = await self.session.scalar(
            select(func.count())
            .select_from(DeploymentReceipt)
            .where(
                DeploymentReceipt.tenant_id == self.context.tenant_id,
                DeploymentReceipt.site_id == site_id,
                DeploymentReceipt.deployed_at >= start_of_day,
            )
        )
        today_count = int(count or 0)

        return GovernanceStatusRead(
            site_id=site.id,
            mode=site.mode,
            autopilot_enabled=site.autopilot_enabled,
            emergency_freeze=site.emergency_freeze,
            daily_change_budget=site.daily_change_budget,
            required_approver_count=site.required_approver_count,
            today_deployments_count=today_count,
            freeze_window_start=site.freeze_window_start,
            freeze_window_end=site.freeze_window_end,
        )

    async def update_governance_settings(
        self, site_id: UUID, command: GovernanceSettingsUpdate
    ) -> GovernanceStatusRead:
        if self.context.role not in ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only_admins_can_update_governance_settings",
            )

        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        if command.autopilot_enabled is not None:
            site.autopilot_enabled = command.autopilot_enabled
        if command.daily_change_budget is not None:
            site.daily_change_budget = command.daily_change_budget
        if command.clear_required_approver_count:
            site.required_approver_count = None
        elif command.required_approver_count is not None:
            site.required_approver_count = command.required_approver_count
        if command.freeze_window_start is not None:
            site.freeze_window_start = command.freeze_window_start
        if command.freeze_window_end is not None:
            site.freeze_window_end = command.freeze_window_end

        event_payload = {
            "site_id": str(site_id),
            "autopilot_enabled": site.autopilot_enabled,
            "daily_change_budget": site.daily_change_budget,
            "required_approver_count": site.required_approver_count,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="governance.settings_updated",
                resource_type="site",
                resource_id=str(site_id),
                trace_id=self.context.trace_id,
                metadata_json=event_payload,
                event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        return await self.get_governance_status(site_id)

    async def set_site_mode(self, site_id: UUID, command: SiteModeUpdate) -> GovernanceStatusRead:
        """Move a site between observe, recommend and autopilot.

        Mode was settable only at site creation, so a customer choosing to move
        from observing to recommending had no way to say so. It is a governance
        decision, not a setting: it changes what the platform is permitted to do
        to the site, so it is owner/admin only, requires a stated reason, and is
        recorded in the audit trail and the outbox like a freeze.

        Climbing is constrained; descending never is. Observe is always
        reachable, because the way to stop a site being changed must never
        itself be blocked.
        """
        if self.context.role not in ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only_admins_can_change_site_mode",
            )

        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        previous = site.mode
        if previous == command.mode:
            return await self.get_governance_status(site_id)

        if MODE_RANK[command.mode] > MODE_RANK[previous]:
            self._check_mode_ceiling(site, command.mode)

        site.mode = command.mode
        if command.mode != "autopilot":
            # Autopilot authority does not outlive the mode that justified it.
            site.autopilot_enabled = False

        payload = {
            "site_id": str(site_id),
            "previous_mode": previous,
            "mode": site.mode,
            "reason": command.reason,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="governance.site_mode_changed",
                resource_type="site",
                resource_id=str(site_id),
                trace_id=self.context.trace_id,
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        self.session.add(
            OutboxEvent(
                tenant_id=self.context.tenant_id,
                event_type="site.mode_changed.v1",
                event_version=1,
                aggregate_type="site",
                aggregate_id=site.id,
                payload=payload,
            )
        )
        await self.session.flush()
        return await self.get_governance_status(site_id)

    def _check_mode_ceiling(self, site: Site, mode: str) -> None:
        """What a site must already be before it may be given more authority."""
        if site.status != "active" or site.verified_at is None:
            # Ownership is the whole basis for touching someone's site.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="site_must_be_verified_before_raising_mode",
            )
        if site.emergency_freeze:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="site_is_frozen",
            )
        if mode == "autopilot" and not site.autopilot_enabled:
            # Autopilot deploys without a human in the loop. Turning the mode on
            # is not the same act as granting that authority, and doing both in
            # one call would let a single request arrive at unattended changes.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="autopilot_must_be_enabled_before_entering_autopilot_mode",
            )

    async def trigger_emergency_freeze(self, site_id: UUID, notes: str = "") -> GovernanceStatusRead:
        if self.context.role not in FREEZE_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_to_trigger_freeze",
            )

        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        site.emergency_freeze = True
        event_payload = {"site_id": str(site_id), "notes": notes}
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="governance.emergency_freeze_triggered",
                resource_type="site",
                resource_id=str(site_id),
                trace_id=self.context.trace_id,
                metadata_json=event_payload,
                event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        self.session.add(
            OutboxEvent(
                tenant_id=self.context.tenant_id,
                event_type="governance.emergency_freeze.v1",
                event_version=1,
                aggregate_type="site",
                aggregate_id=site.id,
                payload=event_payload,
            )
        )
        await self.session.flush()
        return await self.get_governance_status(site_id)

    async def lift_emergency_freeze(self, site_id: UUID) -> GovernanceStatusRead:
        if self.context.role not in ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only_admins_can_lift_emergency_freeze",
            )

        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        site.emergency_freeze = False
        event_payload = {"site_id": str(site_id)}
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="governance.emergency_freeze_lifted",
                resource_type="site",
                resource_id=str(site_id),
                trace_id=self.context.trace_id,
                metadata_json=event_payload,
                event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        return await self.get_governance_status(site_id)

    async def run_policy_simulation(self, site_id: UUID) -> PolicySimulationRun:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        proposals = await self.session.scalars(
            select(Proposal)
            .where(Proposal.site_id == site_id, Proposal.tenant_id == self.context.tenant_id)
            .limit(100)
        )
        p_list = list(proposals)
        now = datetime.now(UTC)

        sim_output = simulate_policy_on_proposals(
            site_mode=site.mode,
            autopilot_enabled=site.autopilot_enabled,
            emergency_freeze=site.emergency_freeze,
            daily_change_budget=site.daily_change_budget,
            freeze_window_start=site.freeze_window_start,
            freeze_window_end=site.freeze_window_end,
            proposals=p_list,
            now=now,
        )

        run = PolicySimulationRun(
            tenant_id=self.context.tenant_id,
            site_id=site_id,
            evaluated_proposals_count=sim_output["evaluated_proposals_count"],
            auto_deployable_count=sim_output["auto_deployable_count"],
            review_required_count=sim_output["review_required_count"],
            prohibited_count=sim_output["prohibited_count"],
            simulation_results_json=sim_output,
            run_at=now,
        )
        self.session.add(run)

        event_payload = {
            "site_id": str(site_id),
            "simulation_id": str(run.id),
            "evaluated_count": run.evaluated_proposals_count,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="simulation.completed",
                resource_type="policy_simulation_run",
                resource_id=str(run.id),
                trace_id=self.context.trace_id,
                metadata_json=event_payload,
                event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def rollback_deployment(
        self,
        proposal_id: UUID,
        notes: str = "",
        adapter: RollbackAdapter | None = None,
    ) -> RollbackReceipt:
        if self.context.role not in ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_to_rollback_deployment",
            )
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
        if adapter is None:
            # Still the honest answer when no connector is wired: refusing beats
            # writing a receipt that claims an undo nobody performed.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="rollback_connector_not_configured",
            )

        existing = await self.session.scalar(
            select(RollbackReceipt).where(
                RollbackReceipt.tenant_id == self.context.tenant_id,
                RollbackReceipt.deployment_receipt_id == receipt.id,
            )
        )
        if existing is not None:
            return existing

        result = await adapter.rollback(
            RollbackRequest(
                tenant_id=self.context.tenant_id,
                site_id=proposal.site_id,
                proposal_id=proposal.id,
                target_path=proposal.target_path,
                before_content=proposal.before_content,
                deployed_hash=proposal.proposal_hash,
                external_ref=receipt.external_ref,
                manifest_json=dict(receipt.manifest_json or {}),
                notes=notes,
            )
        )

        rollback = RollbackReceipt(
            tenant_id=self.context.tenant_id,
            site_id=proposal.site_id,
            proposal_id=proposal.id,
            deployment_receipt_id=receipt.id,
            restored_hash=result.restored_hash,
            status=result.status,
            external_ref=result.external_ref,
            notes=f"{notes} {result.detail}".strip(),
        )
        self.session.add(rollback)

        # Two different outcomes, and conflating them is what let a receipt
        # claim an undo nobody performed. An adapter that reversed the change
        # itself -- closing a pull request that had never merged -- really has
        # put the site back. An adapter that opened a revert pull request has
        # changed nothing yet: the deployed content is still live, and stays
        # live until a person merges. That receipt is waiting, not finished,
        # and the proposal is still deployed.
        rolled_back = result.status == "applied"
        receipt.status = "rolled_back" if rolled_back else "rollback_pending"
        proposal.status = "failed" if rolled_back else proposal.status
        proposal.updated_at = datetime.now(UTC)

        event_payload = {
            "proposal_id": str(proposal.id),
            "site_id": str(proposal.site_id),
            "deployment_receipt_id": str(receipt.id),
            "external_ref": result.external_ref,
            "detail": result.detail,
            # The audit log is where somebody reconstructs what was actually
            # true at the time, so it says which of the two happened rather
            # than leaving it to be inferred from a free-text detail string.
            "receipt_status": receipt.status,
            "change_reversed": rolled_back,
        }
        action = "proposal.rolled_back" if rolled_back else "proposal.rollback_requested"
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action=action,
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
                event_type=f"{action}.v1",
                event_version=1,
                aggregate_type="proposal",
                aggregate_id=proposal.id,
                payload=event_payload,
            )
        )
        await self.session.flush()
        await self.session.refresh(rollback)
        return rollback
