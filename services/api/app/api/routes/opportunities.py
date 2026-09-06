from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.schemas import (
    CalibrationItemEnvelope,
    CalibrationItemRead,
    CalibrationReviewCreate,
    CalibrationReviewEnvelope,
    CalibrationReviewRead,
    OpportunityEnvelope,
    OpportunityRead,
    OpportunitySuppress,
    ProposalEnvelope,
    ProposalRead,
)
from app.core.auth import TenantContextDependency
from app.core.config import Settings, get_settings
from app.db.session import TenantSession
from app.domain.github_adapter import GitHubDeploymentAdapter, GitHubDeploymentError
from app.services.calibrations import CalibrationService
from app.services.github_connector import credential_for_opportunity
from app.services.opportunities import OpportunityService
from app.services.proposal_drafts import ProposalDraftService
from app.services.proposals import ProposalService

router = APIRouter(prefix="/v1/opportunities", tags=["opportunities"])
calibration_router = APIRouter(prefix="/v1/calibration-items", tags=["calibrations"])


@router.get("/{opportunity_id}", response_model=OpportunityEnvelope)
async def get_opportunity(
    opportunity_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> OpportunityEnvelope:
    opportunity = await OpportunityService(session, context).get(opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="opportunity_not_found")
    return OpportunityEnvelope(
        data=OpportunityRead.model_validate(opportunity),
        meta={"trace_id": context.trace_id},
    )


@router.post("/{opportunity_id}/suppress", response_model=OpportunityEnvelope)
async def suppress_opportunity(
    opportunity_id: UUID,
    command: OpportunitySuppress,
    context: TenantContextDependency,
    session: TenantSession,
) -> OpportunityEnvelope:
    opportunity = await OpportunityService(session, context).suppress(opportunity_id, command)
    return OpportunityEnvelope(
        data=OpportunityRead.model_validate(opportunity),
        meta={"trace_id": context.trace_id},
    )


@router.post("/{opportunity_id}/unsuppress", response_model=OpportunityEnvelope)
async def unsuppress_opportunity(
    opportunity_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> OpportunityEnvelope:
    opportunity = await OpportunityService(session, context).unsuppress(opportunity_id)
    return OpportunityEnvelope(
        data=OpportunityRead.model_validate(opportunity),
        meta={"trace_id": context.trace_id},
    )


@calibration_router.get("/{item_id}", response_model=CalibrationItemEnvelope)
async def get_calibration_item(
    item_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> CalibrationItemEnvelope:
    item = await CalibrationService(session, context).get_item(item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="calibration_item_not_found")
    return CalibrationItemEnvelope(
        data=CalibrationItemRead.model_validate(item), meta={"trace_id": context.trace_id}
    )


@calibration_router.post(
    "/{item_id}/reviews",
    response_model=CalibrationReviewEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def review_calibration_item(
    item_id: UUID,
    command: CalibrationReviewCreate,
    context: TenantContextDependency,
    session: TenantSession,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> CalibrationReviewEnvelope:
    review = await CalibrationService(session, context).review_item(
        item_id, command, idempotency_key
    )
    return CalibrationReviewEnvelope(
        data=CalibrationReviewRead.model_validate(review), meta={"trace_id": context.trace_id}
    )


@router.post(
    "/{opportunity_id}/proposal-draft",
    response_model=ProposalEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def draft_proposal_from_opportunity(
    opportunity_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProposalEnvelope:
    """Build the change this opportunity implies, and submit it as a proposal.

    The draft goes through `create_proposal` like any hand-authored one, so it
    is classified, validated and left awaiting approval. Drafting is not
    approving, and this endpoint deploys nothing.
    """
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=httpx.Timeout(20.0)
    ) as client:
        credential = await credential_for_opportunity(
            session, context, opportunity_id, settings, client
        )
        adapter = GitHubDeploymentAdapter(client, credential.target, credential.token)
        try:
            # The path template belongs to the site's connector: two sites in
            # one tenant can be built from repositories with different layouts.
            site_id, command = await ProposalDraftService(
                session, context, credential.path_template
            ).draft_from_opportunity(opportunity_id, adapter.read_file)
        except GitHubDeploymentError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error

    proposal = await ProposalService(session, context).create_proposal(site_id, command)
    return ProposalEnvelope(
        data=ProposalRead.model_validate(proposal),
        meta={"trace_id": context.trace_id},
    )
