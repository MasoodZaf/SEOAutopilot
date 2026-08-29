from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.schemas import (
    CalibrationRunCreate,
    CalibrationRunEnvelope,
    CalibrationRunRead,
    CrawlCreate,
    CrawlEnvelope,
    CrawlRead,
    OpportunityCollection,
    OpportunityRead,
    PageCollection,
    PageRead,
    PerformanceObservationRead,
    PerformanceRunEnvelope,
    PerformanceRunRead,
    PerformanceSummaryEnvelope,
    PerformanceSummaryRead,
    SearchPerformanceEnvelope,
    SearchPerformanceRead,
    SiteCollection,
    SiteCreate,
    SiteEnvelope,
    SiteRead,
    SiteVerify,
    VerificationChallengeEnvelope,
    VerificationChallengeRead,
)
from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.core.cursors import CursorError, decode_page_cursor, encode_page_cursor
from app.db.session import TenantSession
from app.services.calibrations import CalibrationService
from app.services.opportunities import OpportunityService
from app.services.pages import PageService
from app.services.performance import PerformanceService
from app.services.search_performance import SearchPerformanceService
from app.services.sites import SiteService
from app.services.verification import DnsTxtVerifier, get_site_verifier

router = APIRouter(prefix="/v1/sites", tags=["sites"])


@router.post("/{site_id}/performance-runs", response_model=PerformanceRunEnvelope, status_code=status.HTTP_202_ACCEPTED)
async def create_performance_run(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> PerformanceRunEnvelope:
    run = await PerformanceService(session, context).create_run(site_id, idempotency_key)
    return PerformanceRunEnvelope(data=PerformanceRunRead.model_validate(run), meta={"trace_id": context.trace_id})


@router.get("/{site_id}/performance-runs/latest", response_model=PerformanceRunEnvelope)
async def get_latest_performance_run(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> PerformanceRunEnvelope:
    result = await PerformanceService(session, context).latest(site_id)
    if result is None:
        raise HTTPException(status_code=404, detail="performance_run_not_found")
    run, observation = result
    read = PerformanceRunRead.model_validate(run).model_copy(
        update={
            "observation": PerformanceObservationRead.model_validate(observation)
            if observation is not None
            else None
        }
    )
    return PerformanceRunEnvelope(data=read, meta={"trace_id": context.trace_id})


@router.get("/{site_id}/performance-summary", response_model=PerformanceSummaryEnvelope)
async def get_performance_summary(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> PerformanceSummaryEnvelope:
    summary = await PerformanceService(session, context).summary(site_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="performance_summary_not_found")
    return PerformanceSummaryEnvelope(
        data=PerformanceSummaryRead.model_validate(summary), meta={"trace_id": context.trace_id}
    )


@router.post(
    "/{site_id}/calibrations",
    response_model=CalibrationRunEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_calibration_run(
    site_id: UUID,
    command: CalibrationRunCreate,
    context: TenantContextDependency,
    session: TenantSession,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> CalibrationRunEnvelope:
    run = await CalibrationService(session, context).create_run(
        site_id,
        target_size=command.target_size,
        idempotency_key=idempotency_key,
    )
    return CalibrationRunEnvelope(
        data=CalibrationRunRead.model_validate(run), meta={"trace_id": context.trace_id}
    )


@router.get("/{site_id}/calibrations/current", response_model=CalibrationRunEnvelope)
async def get_current_calibration_run(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> CalibrationRunEnvelope:
    run = await CalibrationService(session, context).current_run(site_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="calibration_run_not_found")
    return CalibrationRunEnvelope(
        data=CalibrationRunRead.model_validate(run), meta={"trace_id": context.trace_id}
    )


@router.get("/{site_id}/opportunities", response_model=OpportunityCollection)
async def list_opportunities(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = 20,
    opportunity_status: str = Query(default="open", alias="status"),
    opportunity_type: str | None = Query(default=None, alias="type"),
    min_score: float | None = Query(default=None, alias="min_score"),
) -> OpportunityCollection:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_limit")
    if opportunity_status not in {"open", "shortlisted", "proposing", "proposed", "suppressed"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_status")
    opportunity_service = OpportunityService(session, context)
    opportunities = await opportunity_service.list_top(
        site_id,
        limit,
        opportunity_status,
        opportunity_type=opportunity_type,
        min_score=min_score,
    )
    if opportunities is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    page_urls = await opportunity_service.page_urls(site_id, opportunities)
    return OpportunityCollection(
        data=[
            OpportunityRead.model_validate(item).model_copy(
                update={"page_url": page_urls.get(item.page_id)}
            )
            for item in opportunities
        ],
        meta={"trace_id": context.trace_id, "count": len(opportunities)},
    )


@router.get("/{site_id}/pages", response_model=PageCollection)
async def list_pages(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = 100,
    cursor: str | None = None,
) -> PageCollection:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_limit")
    decoded_cursor = None
    if cursor:
        try:
            decoded_cursor = decode_page_cursor(cursor, site_id, get_settings().cursor_signing_key)
        except CursorError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(error),
            ) from error
    result = await PageService(session, context).list_pages(site_id, limit, decoded_cursor)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    pages, next_cursor = result
    return PageCollection(
        data=[PageRead.model_validate(page) for page in pages],
        meta={
            "trace_id": context.trace_id,
            "count": len(pages),
            "next_cursor": (
                encode_page_cursor(next_cursor, get_settings().cursor_signing_key)
                if next_cursor
                else None
            ),
        },
    )


@router.get("", response_model=SiteCollection)
async def list_sites(context: TenantContextDependency, session: TenantSession) -> SiteCollection:
    sites = await SiteService(session, context).list_sites()
    return SiteCollection(
        data=[SiteRead.model_validate(site) for site in sites], meta={"trace_id": context.trace_id}
    )


@router.post("", response_model=SiteEnvelope, status_code=status.HTTP_201_CREATED)
async def create_site(
    command: SiteCreate,
    context: TenantContextDependency,
    session: TenantSession,
    response: Response,
) -> SiteEnvelope:
    site = await SiteService(session, context).create_site(command)
    response.headers["ETag"] = f'"{site.version}"'
    return SiteEnvelope(data=SiteRead.model_validate(site), meta={"trace_id": context.trace_id})


@router.get("/{site_id}", response_model=SiteEnvelope)
async def get_site(
    site_id: UUID, context: TenantContextDependency, session: TenantSession, response: Response
) -> SiteEnvelope:
    site = await SiteService(session, context).get_site(site_id)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    response.headers["ETag"] = f'"{site.version}"'
    return SiteEnvelope(data=SiteRead.model_validate(site), meta={"trace_id": context.trace_id})


@router.post(
    "/{site_id}/verification-challenges",
    response_model=VerificationChallengeEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_verification_challenge(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> VerificationChallengeEnvelope:
    site = await SiteService(session, context).get_site(site_id)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    challenge, token = await SiteService(session, context).create_verification_challenge(site_id)
    return VerificationChallengeEnvelope(
        data=VerificationChallengeRead(
            id=challenge.id,
            method="dns_txt",
            record_name=f"_seo-autopilot.{site.normalized_host}",
            record_value=f"seo-autopilot-verification={token}",
            token=token,
            expires_at=challenge.expires_at,
        ),
        meta={"trace_id": context.trace_id},
    )


@router.post("/{site_id}/verify", response_model=SiteEnvelope)
async def verify_site(
    site_id: UUID,
    command: SiteVerify,
    context: TenantContextDependency,
    session: TenantSession,
    verifier: Annotated[DnsTxtVerifier, Depends(get_site_verifier)],
) -> SiteEnvelope:
    site = await SiteService(session, context).verify_site(site_id, command.token, verifier)
    return SiteEnvelope(data=SiteRead.model_validate(site), meta={"trace_id": context.trace_id})


@router.post(
    "/{site_id}/crawls", response_model=CrawlEnvelope, status_code=status.HTTP_202_ACCEPTED
)
async def create_crawl(
    site_id: UUID,
    command: CrawlCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> CrawlEnvelope:
    crawl = await SiteService(session, context).create_crawl(site_id, command)
    return CrawlEnvelope(data=CrawlRead.model_validate(crawl), meta={"trace_id": context.trace_id})


@router.get("/{site_id}/crawls/latest", response_model=CrawlEnvelope)
async def get_latest_crawl(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> CrawlEnvelope:
    crawl = await SiteService(session, context).latest_crawl(site_id)
    if crawl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="crawl_not_found")
    return CrawlEnvelope(data=CrawlRead.model_validate(crawl), meta={"trace_id": context.trace_id})


@router.get("/{site_id}/search-performance", response_model=SearchPerformanceEnvelope)
async def get_search_performance(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    range_start: Annotated[date | None, Query()] = None,
    range_end: Annotated[date | None, Query()] = None,
) -> SearchPerformanceEnvelope:
    end = range_end or (datetime.now(UTC).date() - timedelta(days=2))
    start = range_start or (end - timedelta(days=27))
    summary = await SearchPerformanceService(session, context).summarize(site_id, start, end)
    return SearchPerformanceEnvelope(
        data=SearchPerformanceRead.model_validate(summary), meta={"trace_id": context.trace_id}
    )
