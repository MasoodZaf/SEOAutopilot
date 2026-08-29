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
from app.domain.governance import (
    simulate_policy_on_proposals,
)

ADMIN_ROLES = {Role.OWNER, Role.ADMIN}
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
        if command.freeze_window_start is not None:
            site.freeze_window_start = command.freeze_window_start
        if command.freeze_window_end is not None:
            site.freeze_window_end = command.freeze_window_end

        event_payload = {
            "site_id": str(site_id),
            "autopilot_enabled": site.autopilot_enabled,
            "daily_change_budget": site.daily_change_budget,
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
        await self.session.commit()
        return await self.get_governance_status(site_id)

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
        await self.session.commit()
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
        await self.session.commit()
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
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def rollback_deployment(self, proposal_id: UUID, notes: str = "") -> RollbackReceipt:
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="rollback_connector_not_configured",
        )
