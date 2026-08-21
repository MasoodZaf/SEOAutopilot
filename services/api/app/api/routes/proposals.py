from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.api.schemas import (
    DeploymentCreate,
    DeploymentReceiptEnvelope,
    DeploymentReceiptRead,
    ProposalApprovalCreate,
    ProposalApprovalEnvelope,
    ProposalApprovalRead,
    ProposalCollection,
    ProposalCreate,
    ProposalEnvelope,
    ProposalRead,
)
from app.core.auth import TenantContextDependency
from app.db.session import TenantSession
from app.domain.deployments import MockDeploymentAdapter
from app.services.proposals import ProposalService

router = APIRouter(tags=["proposals"])


@router.post(
    "/v1/sites/{site_id}/proposals",
    response_model=ProposalEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    site_id: UUID,
    command: ProposalCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ProposalEnvelope:
    proposal = await ProposalService(session, context).create_proposal(site_id, command)
    return ProposalEnvelope(
        data=ProposalRead.model_validate(proposal),
        meta={"trace_id": context.trace_id},
    )


@router.get(
    "/v1/sites/{site_id}/proposals",
    response_model=ProposalCollection,
)
async def list_proposals(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    status_filter: str | None = Query(default=None, alias="status"),
    risk_filter: str | None = Query(default=None, alias="risk"),
    limit: int = Query(default=50, ge=1, le=100),
) -> ProposalCollection:
    proposals = await ProposalService(session, context).list_proposals(
        site_id=site_id,
        status_filter=status_filter,
        risk_filter=risk_filter,
        limit=limit,
    )
    return ProposalCollection(
        data=[ProposalRead.model_validate(p) for p in proposals],
        meta={"trace_id": context.trace_id, "count": len(proposals)},
    )


@router.get(
    "/v1/proposals/{proposal_id}",
    response_model=ProposalEnvelope,
)
async def get_proposal(
    proposal_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> ProposalEnvelope:
    proposal = await ProposalService(session, context).get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal_not_found")
    return ProposalEnvelope(
        data=ProposalRead.model_validate(proposal),
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/v1/proposals/{proposal_id}/approvals",
    response_model=ProposalApprovalEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def approve_proposal(
    proposal_id: UUID,
    command: ProposalApprovalCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ProposalApprovalEnvelope:
    approval = await ProposalService(session, context).approve_proposal(proposal_id, command)
    return ProposalApprovalEnvelope(
        data=ProposalApprovalRead.model_validate(approval),
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/v1/proposals/{proposal_id}/deploy",
    response_model=DeploymentReceiptEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def deploy_proposal(
    proposal_id: UUID,
    command: DeploymentCreate,
    context: TenantContextDependency,
    session: TenantSession,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> DeploymentReceiptEnvelope:
    adapter = MockDeploymentAdapter(connector_type=command.connector_type, enforce_drift=True)
    receipt = await ProposalService(session, context).deploy_proposal(
        proposal_id=proposal_id,
        command=command,
        idempotency_key=idempotency_key,
        adapter=adapter,
    )
    return DeploymentReceiptEnvelope(
        data=DeploymentReceiptRead.model_validate(receipt),
        meta={"trace_id": context.trace_id},
    )
