from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    AiCitationEnvelope,
    AiCitationObservationRead,
    AiCitationPromptCollection,
    AiCitationPromptCreate,
    AiCitationPromptEnvelope,
    AiCitationPromptRead,
    AiCitationPromptSuggestion,
    AiCitationReport,
    AiCitationRunRead,
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
    ProposalEnvelope,
    ProposalRead,
)
from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.db.session import TenantSession
from app.services.ai_citations import AiCitationService
from app.services.competitors import CompetitorService
from app.services.llms_txt import LlmsTxtService

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


@router.post(
    "/sites/{site_id}/llms-txt/proposal",
    response_model=ProposalEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def propose_llms_txt(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> ProposalEnvelope:
    """Propose an llms.txt built from the last crawl, when the site serves none.

    It is a new-file proposal like any other: high risk, approved by two people
    who did not create it, and deployed as a pull request a person merges.
    """
    proposal = await LlmsTxtService(session, context, get_settings()).propose(site_id)
    return ProposalEnvelope(
        data=ProposalRead.model_validate(proposal), meta={"trace_id": context.trace_id}
    )


@router.get("/sites/{site_id}/ai-citation-prompts", response_model=AiCitationPromptCollection)
async def list_ai_citation_prompts(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> AiCitationPromptCollection:
    """The questions tracked for observed AI citations, plus untracked suggestions."""
    tracked, suggestions = await AiCitationService(session, context).prompts(site_id)
    return AiCitationPromptCollection(
        data=[AiCitationPromptRead.model_validate(item) for item in tracked],
        suggestions=[AiCitationPromptSuggestion(**item) for item in suggestions],
        meta={"trace_id": context.trace_id, "count": len(tracked)},
    )


@router.post(
    "/sites/{site_id}/ai-citation-prompts",
    response_model=AiCitationPromptEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def track_ai_citation_prompt(
    site_id: UUID,
    command: AiCitationPromptCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> AiCitationPromptEnvelope:
    prompt = await AiCitationService(session, context).track(
        site_id, command.prompt, command.keyword_cluster_id
    )
    return AiCitationPromptEnvelope(
        data=AiCitationPromptRead.model_validate(prompt), meta={"trace_id": context.trace_id}
    )


@router.delete(
    "/sites/{site_id}/ai-citation-prompts/{prompt_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def untrack_ai_citation_prompt(
    site_id: UUID, prompt_id: UUID, context: TenantContextDependency, session: TenantSession
) -> None:
    await AiCitationService(session, context).untrack(site_id, prompt_id)


@router.get("/sites/{site_id}/ai-citations", response_model=AiCitationEnvelope)
async def read_ai_citations(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> AiCitationEnvelope:
    """What the answer engines cited in the latest run, and earlier runs' totals.

    Observed through each provider's API with web search; answers vary between
    runs, so counts are "cited in N of M answers", never a rank.
    """
    latest, observations, history = await AiCitationService(session, context).report(site_id)
    return AiCitationEnvelope(
        data=AiCitationReport(
            latest=AiCitationRunRead.model_validate(latest) if latest else None,
            observations=[AiCitationObservationRead.model_validate(item) for item in observations],
            history=[AiCitationRunRead.model_validate(item) for item in history],
        ),
        meta={"trace_id": context.trace_id},
    )
