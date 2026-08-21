from uuid import UUID

from fastapi import APIRouter, Body, status

from app.api.schemas import (
    GovernanceSettingsUpdate,
    GovernanceStatusEnvelope,
    PolicySimulationEnvelope,
    PolicySimulationRead,
    RollbackReceiptEnvelope,
    RollbackReceiptRead,
)
from app.core.auth import TenantContextDependency
from app.db.session import TenantSession
from app.services.governance import GovernanceService

router = APIRouter(tags=["governance"])


@router.get(
    "/v1/sites/{site_id}/governance",
    response_model=GovernanceStatusEnvelope,
)
async def get_site_governance(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> GovernanceStatusEnvelope:
    status_read = await GovernanceService(session, context).get_governance_status(site_id)
    return GovernanceStatusEnvelope(
        data=status_read,
        meta={"trace_id": context.trace_id},
    )


@router.patch(
    "/v1/sites/{site_id}/governance",
    response_model=GovernanceStatusEnvelope,
)
async def update_site_governance(
    site_id: UUID,
    command: GovernanceSettingsUpdate,
    context: TenantContextDependency,
    session: TenantSession,
) -> GovernanceStatusEnvelope:
    status_read = await GovernanceService(session, context).update_governance_settings(site_id, command)
    return GovernanceStatusEnvelope(
        data=status_read,
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/v1/sites/{site_id}/governance/freeze",
    response_model=GovernanceStatusEnvelope,
    status_code=status.HTTP_200_OK,
)
async def trigger_emergency_freeze(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    notes: str = Body(default="", embed=True),
) -> GovernanceStatusEnvelope:
    status_read = await GovernanceService(session, context).trigger_emergency_freeze(site_id, notes)
    return GovernanceStatusEnvelope(
        data=status_read,
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/v1/sites/{site_id}/governance/unfreeze",
    response_model=GovernanceStatusEnvelope,
    status_code=status.HTTP_200_OK,
)
async def lift_emergency_freeze(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> GovernanceStatusEnvelope:
    status_read = await GovernanceService(session, context).lift_emergency_freeze(site_id)
    return GovernanceStatusEnvelope(
        data=status_read,
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/v1/sites/{site_id}/simulation",
    response_model=PolicySimulationEnvelope,
    status_code=status.HTTP_200_OK,
)
async def run_policy_simulation(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> PolicySimulationEnvelope:
    sim_run = await GovernanceService(session, context).run_policy_simulation(site_id)
    return PolicySimulationEnvelope(
        data=PolicySimulationRead.model_validate(sim_run),
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/v1/proposals/{proposal_id}/rollback",
    response_model=RollbackReceiptEnvelope,
    status_code=status.HTTP_200_OK,
)
async def rollback_proposal_deployment(
    proposal_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    notes: str = Body(default="", embed=True),
) -> RollbackReceiptEnvelope:
    rollback = await GovernanceService(session, context).rollback_deployment(proposal_id, notes)
    return RollbackReceiptEnvelope(
        data=RollbackReceiptRead.model_validate(rollback),
        meta={"trace_id": context.trace_id},
    )
