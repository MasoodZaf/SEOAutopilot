from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    AiVisibilityCollection,
    AiVisibilityRead,
    CompetitorCollection,
    CompetitorCreate,
    CompetitorEnvelope,
    CompetitorObservationRead,
    CompetitorPageCollection,
    CompetitorPageCreate,
    CompetitorPageEnvelope,
    CompetitorPageRead,
    CompetitorRead,
    CompetitorScanEnvelope,
    CompetitorScanRead,
)
from app.core.auth import TenantContextDependency
from app.db.session import TenantSession
from app.services.competitors import CompetitorService

router = APIRouter(prefix="/v1", tags=["competitors"])


@router.get("/sites/{site_id}/competitors", response_model=CompetitorCollection)
async def list_competitors(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> CompetitorCollection:
    competitors = await CompetitorService(session, context).list_competitors(site_id)
    if competitors is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return CompetitorCollection(
        data=[CompetitorRead.model_validate(item) for item in competitors],
        meta={"trace_id": context.trace_id, "count": len(competitors)},
    )


@router.post(
    "/sites/{site_id}/competitors",
    response_model=CompetitorEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def add_competitor(
    site_id: UUID,
    command: CompetitorCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> CompetitorEnvelope:
    competitor = await CompetitorService(session, context).create_competitor(site_id, command)
    return CompetitorEnvelope(
        data=CompetitorRead.model_validate(competitor), meta={"trace_id": context.trace_id}
    )


@router.post("/competitors/{competitor_id}/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause_competitor(
    competitor_id: UUID, context: TenantContextDependency, session: TenantSession
) -> None:
    await CompetitorService(session, context).remove_competitor(competitor_id)


@router.get("/competitors/{competitor_id}/pages", response_model=CompetitorPageCollection)
async def list_competitor_pages(
    competitor_id: UUID, context: TenantContextDependency, session: TenantSession
) -> CompetitorPageCollection:
    pages = await CompetitorService(session, context).list_pages(competitor_id)
    if pages is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="competitor_not_found")
    return CompetitorPageCollection(
        data=[CompetitorPageRead.model_validate(item) for item in pages],
        meta={"trace_id": context.trace_id, "count": len(pages)},
    )


@router.post(
    "/competitors/{competitor_id}/pages",
    response_model=CompetitorPageEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def add_competitor_page(
    competitor_id: UUID,
    command: CompetitorPageCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> CompetitorPageEnvelope:
    """The scan fetches exactly these URLs and performs no discovery."""
    page = await CompetitorService(session, context).add_page(competitor_id, command)
    return CompetitorPageEnvelope(
        data=CompetitorPageRead.model_validate(page), meta={"trace_id": context.trace_id}
    )


@router.get("/sites/{site_id}/competitor-scans/latest", response_model=CompetitorScanEnvelope)
async def latest_competitor_scan(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> CompetitorScanEnvelope:
    found = await CompetitorService(session, context).latest_scan(site_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="competitor_scan_not_available"
        )
    scan, observations = found
    return CompetitorScanEnvelope(
        data=CompetitorScanRead.model_validate(scan),
        observations=[CompetitorObservationRead.model_validate(item) for item in observations],
        meta={"trace_id": context.trace_id, "count": len(observations)},
    )


@router.get("/sites/{site_id}/ai-visibility", response_model=AiVisibilityCollection)
async def list_ai_visibility(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=30, ge=1, le=90),
) -> AiVisibilityCollection:
    """Readiness measured from first-party evidence; not observed citations."""
    snapshots = await CompetitorService(session, context).ai_visibility_history(site_id, limit)
    if snapshots is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return AiVisibilityCollection(
        data=[AiVisibilityRead.model_validate(item) for item in snapshots],
        meta={
            "trace_id": context.trace_id,
            "count": len(snapshots),
            "citation_source": "none",
        },
    )
