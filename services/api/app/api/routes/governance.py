from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.api.routes.proposals import github_target
from app.api.schemas import (
    GovernanceSettingsUpdate,
    GovernanceStatusEnvelope,
    PolicySimulationEnvelope,
    PolicySimulationRead,
    RollbackReceiptEnvelope,
    RollbackReceiptRead,
    SiteModeUpdate,
)
from app.core.auth import TenantContextDependency
from app.core.config import Settings, get_settings
from app.db.session import TenantSession
from app.domain.github_adapter import GitHubDeploymentAdapter, GitHubDeploymentError
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


@router.patch(
    "/v1/sites/{site_id}/governance/mode",
    response_model=GovernanceStatusEnvelope,
)
async def set_site_mode(
    site_id: UUID,
    command: SiteModeUpdate,
    context: TenantContextDependency,
    session: TenantSession,
) -> GovernanceStatusEnvelope:
    """Change what the platform is allowed to do to this site."""
    status_read = await GovernanceService(session, context).set_site_mode(site_id, command)
    return GovernanceStatusEnvelope(data=status_read, meta={"trace_id": context.trace_id})


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
    settings: Annotated[Settings, Depends(get_settings)],
    notes: str = Body(default="", embed=True),
) -> RollbackReceiptEnvelope:
    target = github_target(settings)
    token = settings.github_token
    if target is None or token is None:
        # No connector, no rollback. The service refuses rather than recording
        # an undo that never happened.
        rollback = await GovernanceService(session, context).rollback_deployment(
            proposal_id, notes
        )
    else:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(20.0)
        ) as client:
            try:
                rollback = await GovernanceService(session, context).rollback_deployment(
                    proposal_id,
                    notes,
                    GitHubDeploymentAdapter(client, target, token.get_secret_value()),
                )
            except GitHubDeploymentError as error:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
                ) from error
    return RollbackReceiptEnvelope(
        data=RollbackReceiptRead.model_validate(rollback),
        meta={"trace_id": context.trace_id},
    )
