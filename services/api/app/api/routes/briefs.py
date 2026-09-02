from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    ContentBriefCollection,
    ContentBriefEnvelope,
    ContentBriefKind,
    ContentBriefRead,
    ContentBriefStatus,
    ContentBriefStatusUpdate,
    ContentBriefSummary,
)
from app.core.auth import TenantContextDependency
from app.db.session import TenantSession
from app.services.briefs import ContentBriefService

router = APIRouter(prefix="/v1", tags=["content-briefs"])

# `status` is the natural query name but shadows fastapi.status inside the
# handler, so the parameter is declared once here.
BRIEF_STATUS_QUERY = Query(default=None, alias="status")


@router.get("/sites/{site_id}/content-briefs", response_model=ContentBriefCollection)
async def list_content_briefs(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=25, ge=1, le=100),
    brief_status: ContentBriefStatus | None = BRIEF_STATUS_QUERY,
    kind: ContentBriefKind | None = None,
    answer_engine_only: bool = False,
) -> ContentBriefCollection:
    """The refresh queue is this list filtered to `kind=refresh`, priority first."""
    briefs = await ContentBriefService(session, context).list_for_site(
        site_id,
        limit,
        brief_status.value if brief_status else None,
        kind.value if kind else None,
        answer_engine_only,
    )
    if briefs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return ContentBriefCollection(
        data=[ContentBriefSummary.model_validate(item) for item in briefs],
        meta={"trace_id": context.trace_id, "count": len(briefs)},
    )


@router.get("/content-briefs/{brief_id}", response_model=ContentBriefEnvelope)
async def get_content_brief(
    brief_id: UUID, context: TenantContextDependency, session: TenantSession
) -> ContentBriefEnvelope:
    brief = await ContentBriefService(session, context).get(brief_id)
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="content_brief_not_found"
        )
    return ContentBriefEnvelope(
        data=ContentBriefRead.model_validate(brief), meta={"trace_id": context.trace_id}
    )


@router.patch("/content-briefs/{brief_id}", response_model=ContentBriefEnvelope)
async def update_content_brief_status(
    brief_id: UUID,
    command: ContentBriefStatusUpdate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ContentBriefEnvelope:
    brief = await ContentBriefService(session, context).update_status(brief_id, command)
    return ContentBriefEnvelope(
        data=ContentBriefRead.model_validate(brief), meta={"trace_id": context.trace_id}
    )
