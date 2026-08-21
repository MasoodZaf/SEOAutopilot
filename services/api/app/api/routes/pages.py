from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import (
    PageDetail,
    PageEnvelope,
    PageObservationRead,
    PageRead,
    TechnicalFindingRead,
)
from app.core.auth import TenantContextDependency
from app.db.models import PageObservation
from app.db.session import TenantSession
from app.services.pages import PageService

router = APIRouter(prefix="/v1/pages", tags=["pages"])


def observation_response(observation: PageObservation) -> PageObservationRead:
    return PageObservationRead(
        id=observation.id,
        crawl_job_id=observation.crawl_job_id,
        observed_at=observation.observed_at,
        http_status=observation.http_status,
        final_url=observation.final_url,
        title=observation.title,
        meta_description=observation.meta_description,
        h1=observation.h1_json,
        word_count=observation.word_count,
        content_hash=observation.content_hash,
        rendered=observation.rendered,
        canonical_url=observation.canonical_url,
        robots_directives=observation.robots_directives,
        structured_data=observation.structured_data_json,
        link_count_total=observation.link_count_total,
        links_truncated=observation.links_truncated,
    )


@router.get("/{page_id}", response_model=PageEnvelope)
async def get_page(
    page_id: UUID, context: TenantContextDependency, session: TenantSession
) -> PageEnvelope:
    result = await PageService(session, context).get_page(page_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page_not_found")
    page = result.page
    observation = result.observation
    score = result.score
    latest = None
    if observation is not None:
        latest = observation_response(observation)
    findings = [
        TechnicalFindingRead(
            id=finding.id,
            code=finding.rule_key,
            severity=finding.severity,
            summary=finding.summary,
            confidence=finding.confidence,
            evidence_refs=finding.evidence_refs,
        )
        for finding in result.findings
    ]
    base = PageRead.model_validate(page)
    return PageEnvelope(
        data=PageDetail(
            **base.model_dump(),
            latest_observation=latest,
            technical_score=score.score if score else None,
            scoring_version=result.scoring_version,
            score_calculated_at=score.calculated_at if score else None,
            score_evidence_cutoff=score.evidence_cutoff if score else None,
            findings=findings,
        ),
        meta={"trace_id": context.trace_id},
    )
