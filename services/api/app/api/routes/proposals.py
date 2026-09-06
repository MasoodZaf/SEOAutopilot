from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BatchDeploymentCreate,
    DeploymentCreate,
    DeploymentReceiptCollection,
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
from app.core.config import Settings, get_settings
from app.core.context import TenantContext
from app.db.session import TenantSession
from app.domain.deployments import DeploymentAdapter, MockDeploymentAdapter
from app.domain.github_adapter import (
    GitHubDeploymentAdapter,
    GitHubDeploymentError,
    GitHubTarget,
)
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
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeploymentReceiptEnvelope:
    if command.connector_type == "github":
        target = github_target(settings)
        token = settings.github_token
        if target is None or token is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="deployment_connector_not_configured",
            )
        # Redirects are never followed: a redirect off api.github.com would
        # carry the installation token to wherever it pointed.
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(20.0)
        ) as client:
            return await _deploy(
                proposal_id,
                command,
                context,
                session,
                idempotency_key,
                settings,
                GitHubDeploymentAdapter(client, target, token.get_secret_value()),
            )

    # The mock adapter reports a deployment nobody performed, so reaching it
    # takes a deliberate opt-in rather than an inference from app_env. The
    # production host runs app_env=development until real identity lands, and
    # inferring from it would put the mock one flag flip away from live.
    if command.connector_type != "mock" or not settings.mock_deployments_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="deployment_connector_not_configured",
        )
    return await _deploy(
        proposal_id,
        command,
        context,
        session,
        idempotency_key,
        settings,
        MockDeploymentAdapter(connector_type="mock", enforce_drift=True),
    )


def github_target(settings: Settings) -> GitHubTarget | None:
    """Parse `owner/repository` from settings, or nothing if it is unusable."""
    raw = (settings.github_repository or "").strip()
    owner, separator, repository = raw.partition("/")
    if not separator or not owner or not repository or "/" in repository:
        return None
    return GitHubTarget(
        owner=owner, repository=repository, base_branch=settings.github_base_branch
    )


async def _deploy(
    proposal_id: UUID,
    command: DeploymentCreate,
    context: TenantContext,
    session: AsyncSession,
    idempotency_key: str,
    settings: Settings,
    adapter: DeploymentAdapter,
) -> DeploymentReceiptEnvelope:
    """The gate is the same whichever adapter runs; only the effect differs."""
    try:
        receipt = await ProposalService(
            session,
            context,
            deployments_enabled=settings.deployments_enabled,
        ).deploy_proposal(
            proposal_id=proposal_id,
            command=command,
            idempotency_key=idempotency_key,
            adapter=adapter,
        )
    except GitHubDeploymentError as error:
        # The provider's own failure, not the caller's. Surfacing the code keeps
        # the receipt and the response saying the same thing.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error
    return DeploymentReceiptEnvelope(
        data=DeploymentReceiptRead.model_validate(receipt),
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/v1/sites/{site_id}/deployments",
    response_model=DeploymentReceiptCollection,
    status_code=status.HTTP_202_ACCEPTED,
)
async def deploy_proposals(
    site_id: UUID,
    command: BatchDeploymentCreate,
    context: TenantContextDependency,
    session: TenantSession,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeploymentReceiptCollection:
    """Deploy several approved proposals as one pull request.

    Thirty single-file pull requests describe the same work as one touching
    thirty files, and only the second can be reviewed. Each proposal keeps its
    own approval and its own receipt.
    """
    target = github_target(settings)
    token = settings.github_token
    if target is None or token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="deployment_connector_not_configured",
        )

    async with httpx.AsyncClient(
        follow_redirects=False, timeout=httpx.Timeout(60.0)
    ) as client:
        try:
            receipts = await ProposalService(
                session, context, deployments_enabled=settings.deployments_enabled
            ).deploy_proposals(
                site_id=site_id,
                proposal_ids=command.proposal_ids,
                idempotency_key=idempotency_key,
                adapter=GitHubDeploymentAdapter(client, target, token.get_secret_value()),
            )
        except GitHubDeploymentError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error

    return DeploymentReceiptCollection(
        data=[DeploymentReceiptRead.model_validate(r) for r in receipts],
        meta={"trace_id": context.trace_id, "count": len(receipts)},
    )
