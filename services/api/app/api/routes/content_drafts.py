from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.schemas import (
    ContentDraftCollection,
    ContentDraftCreate,
    ContentDraftEnvelope,
    ContentDraftRead,
    ContentDraftSummary,
    ContentDraftUpdate,
)
from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.db.models import ContentDraft, Proposal
from app.db.session import TenantSession
from app.services.content_drafts import ContentDraftService

router = APIRouter(prefix="/v1", tags=["content-drafts"])


async def _read(session: TenantSession, draft: ContentDraft) -> ContentDraftRead:
    proposal_id = await session.scalar(
        select(Proposal.id)
        .where(Proposal.tenant_id == draft.tenant_id, Proposal.content_draft_id == draft.id)
        .order_by(Proposal.created_at.desc())
        .limit(1)
    )
    return ContentDraftRead.model_validate(draft).model_copy(update={"proposal_id": proposal_id})


@router.post(
    "/content-briefs/{brief_id}/drafts",
    response_model=ContentDraftEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_content_draft(
    brief_id: UUID,
    command: ContentDraftCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ContentDraftEnvelope:
    """Queue an AI draft of a new post. Returns at once; the worker writes it."""
    service = ContentDraftService(session, context, get_settings())
    draft = await service.request(
        brief_id, command.idempotency_key, command.author_name, command.provider
    )
    return ContentDraftEnvelope(
        data=await _read(session, draft), meta={"trace_id": context.trace_id}
    )


@router.get("/sites/{site_id}/content-drafts", response_model=ContentDraftCollection)
async def list_content_drafts(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> ContentDraftCollection:
    drafts = await ContentDraftService(session, context, get_settings()).list_for_site(site_id, limit)
    return ContentDraftCollection(
        data=[ContentDraftSummary.model_validate(item) for item in drafts],
        meta={"trace_id": context.trace_id, "count": len(drafts)},
    )


@router.get("/content-drafts/{draft_id}", response_model=ContentDraftEnvelope)
async def get_content_draft(
    draft_id: UUID, context: TenantContextDependency, session: TenantSession
) -> ContentDraftEnvelope:
    service = ContentDraftService(session, context, get_settings())
    draft = await service.get(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="content_draft_not_found")
    spent = await service.spent_this_month()
    return ContentDraftEnvelope(
        data=await _read(session, draft),
        meta={
            "trace_id": context.trace_id,
            "spent_this_month_micros": spent,
            "monthly_budget_micros": get_settings().content_draft_monthly_budget_micros,
        },
    )


@router.patch("/content-drafts/{draft_id}", response_model=ContentDraftEnvelope)
async def update_content_draft(
    draft_id: UUID,
    command: ContentDraftUpdate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ContentDraftEnvelope:
    draft = await ContentDraftService(session, context, get_settings()).update(
        draft_id,
        expected_version=command.version,
        title=command.title,
        slug=command.slug,
        meta_description=command.meta_description,
        body_markdown=command.body_markdown,
        author_name=command.author_name,
        resolved_flags=command.resolved_flags,
    )
    return ContentDraftEnvelope(data=await _read(session, draft), meta={"trace_id": context.trace_id})


@router.post("/content-drafts/{draft_id}/withdraw", response_model=ContentDraftEnvelope)
async def withdraw_content_draft(
    draft_id: UUID, context: TenantContextDependency, session: TenantSession
) -> ContentDraftEnvelope:
    draft = await ContentDraftService(session, context, get_settings()).withdraw(draft_id)
    return ContentDraftEnvelope(data=await _read(session, draft), meta={"trace_id": context.trace_id})
