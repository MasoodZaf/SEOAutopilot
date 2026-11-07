"""Reddit drafts. Nothing here calls Reddit: a person posts what they approved."""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.schemas import (
    RedditDraftCollection,
    RedditDraftCreate,
    RedditDraftEnvelope,
    RedditDraftRead,
    RedditDraftUpdate,
    RedditHandOff,
    RedditHandOffEnvelope,
    RedditPosted,
    RedditThreadCollection,
    RedditThreadRead,
)
from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.db.models import RedditDraft
from app.db.session import TenantSession
from app.services.billing import RequireSubscription
from app.services.reddit import RedditService

router = APIRouter(prefix="/v1", tags=["reddit"])


def _service(session: TenantSession, context: TenantContextDependency) -> RedditService:
    return RedditService(session, context, get_settings())


def _envelope(draft: RedditDraft, trace_id: str) -> RedditDraftEnvelope:
    return RedditDraftEnvelope(data=RedditDraftRead.model_validate(draft), meta={"trace_id": trace_id})


@router.get("/sites/{site_id}/reddit/threads", response_model=RedditThreadCollection)
async def list_reddit_threads(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> RedditThreadCollection:
    """Reddit threads that answer engines cited for this site's tracked questions."""
    threads = await _service(session, context).threads(site_id)
    return RedditThreadCollection(
        data=[RedditThreadRead.model_validate(item) for item in threads],
        meta={"trace_id": context.trace_id, "count": len(threads)},
    )


@router.get("/sites/{site_id}/reddit/drafts", response_model=RedditDraftCollection)
async def list_reddit_drafts(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> RedditDraftCollection:
    drafts = await _service(session, context).list_for_site(site_id)
    return RedditDraftCollection(
        data=[RedditDraftRead.model_validate(item) for item in drafts],
        meta={"trace_id": context.trace_id, "count": len(drafts)},
    )


@router.post(
    "/sites/{site_id}/reddit/drafts",
    response_model=RedditDraftEnvelope,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireSubscription],
)
async def create_reddit_draft(
    site_id: UUID,
    command: RedditDraftCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> RedditDraftEnvelope:
    """Write a draft with the workspace's own model key. Nothing is posted."""
    draft = await _service(session, context).create(
        site_id,
        thread_url=command.thread_url,
        subreddit=command.subreddit,
        question=command.question,
        context=command.context,
        page_url=command.page_url,
        provider=command.provider,
    )
    return _envelope(draft, context.trace_id)


@router.patch("/reddit-drafts/{draft_id}", response_model=RedditDraftEnvelope)
async def update_reddit_draft(
    draft_id: UUID,
    command: RedditDraftUpdate,
    context: TenantContextDependency,
    session: TenantSession,
) -> RedditDraftEnvelope:
    draft = await _service(session, context).update(draft_id, command.title, command.body)
    return _envelope(draft, context.trace_id)


@router.post("/reddit-drafts/{draft_id}/approve", response_model=RedditDraftEnvelope)
async def approve_reddit_draft(
    draft_id: UUID, context: TenantContextDependency, session: TenantSession
) -> RedditDraftEnvelope:
    draft = await _service(session, context).approve(draft_id)
    return _envelope(draft, context.trace_id)


@router.post("/reddit-drafts/{draft_id}/hand-off", response_model=RedditHandOffEnvelope)
async def hand_off_reddit_draft(
    draft_id: UUID, context: TenantContextDependency, session: TenantSession
) -> RedditHandOffEnvelope:
    """Where to post it and the text to paste. The person presses Post on Reddit."""
    service = _service(session, context)
    hand_off = await service.hand_off(draft_id)
    draft = await service.get(draft_id)
    return RedditHandOffEnvelope(
        data=RedditHandOff(**hand_off),
        draft=RedditDraftRead.model_validate(draft),
        meta={"trace_id": context.trace_id},
    )


@router.post("/reddit-drafts/{draft_id}/posted", response_model=RedditDraftEnvelope)
async def mark_reddit_draft_posted(
    draft_id: UUID,
    command: RedditPosted,
    context: TenantContextDependency,
    session: TenantSession,
) -> RedditDraftEnvelope:
    draft = await _service(session, context).mark_posted(draft_id, command.posted_url)
    return _envelope(draft, context.trace_id)


@router.post("/reddit-drafts/{draft_id}/dismiss", response_model=RedditDraftEnvelope)
async def dismiss_reddit_draft(
    draft_id: UUID, context: TenantContextDependency, session: TenantSession
) -> RedditDraftEnvelope:
    draft = await _service(session, context).dismiss(draft_id)
    return _envelope(draft, context.trace_id)
