from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    KeywordAnalysisRunEnvelope,
    KeywordAnalysisRunRead,
    KeywordClusterCollection,
    KeywordClusterEnvelope,
    KeywordClusterRead,
    KeywordIntent,
    KeywordMemberCollection,
)
from app.core.auth import TenantContextDependency
from app.core.config import Settings, get_settings
from app.db.session import TenantSession
from app.services.connector_secrets import decode_encryption_key
from app.services.keywords import KeywordService

router = APIRouter(prefix="/v1", tags=["keywords"])


def keyword_service(
    settings: Settings, session: TenantSession, context: TenantContextDependency
) -> KeywordService:
    key: bytes | None = None
    if (
        settings.connector_secret_backend == "database_envelope"
        and settings.connector_secret_encryption_key
    ):
        key = decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value())
    return KeywordService(session, context, encryption_key=key)


@router.get("/sites/{site_id}/keyword-clusters", response_model=KeywordClusterCollection)
async def list_keyword_clusters(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=25, ge=1, le=100),
    intent: KeywordIntent | None = None,
    answer_engine_only: bool = False,
) -> KeywordClusterCollection:
    service = keyword_service(get_settings(), session, context)
    found = await service.list_clusters(
        site_id, limit, intent.value if intent else None, answer_engine_only
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="keyword_analysis_not_available"
        )
    run, clusters = found
    return KeywordClusterCollection(
        data=[KeywordClusterRead.model_validate(item) for item in clusters],
        meta={
            "trace_id": context.trace_id,
            "count": len(clusters),
            "analysis_run_id": str(run.id),
            "algorithm_version": run.algorithm_version,
            "window_start": run.window_start.isoformat(),
            "window_end": run.window_end.isoformat(),
            "queries_considered": run.queries_considered,
        },
    )


@router.get("/sites/{site_id}/keyword-analysis/latest", response_model=KeywordAnalysisRunEnvelope)
async def latest_keyword_analysis(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> KeywordAnalysisRunEnvelope:
    service = keyword_service(get_settings(), session, context)
    run = await service.latest_run(site_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="keyword_analysis_not_available"
        )
    return KeywordAnalysisRunEnvelope(
        data=KeywordAnalysisRunRead.model_validate(run), meta={"trace_id": context.trace_id}
    )


@router.get("/keyword-clusters/{cluster_id}", response_model=KeywordClusterEnvelope)
async def get_keyword_cluster(
    cluster_id: UUID, context: TenantContextDependency, session: TenantSession
) -> KeywordClusterEnvelope:
    service = keyword_service(get_settings(), session, context)
    cluster = await service.get_cluster(cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="keyword_cluster_not_found"
        )
    return KeywordClusterEnvelope(
        data=KeywordClusterRead.model_validate(cluster), meta={"trace_id": context.trace_id}
    )


@router.get("/keyword-clusters/{cluster_id}/members", response_model=KeywordMemberCollection)
async def list_keyword_cluster_members(
    cluster_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> KeywordMemberCollection:
    """The only endpoint that reveals stored query terms. Audited on every read."""
    service = keyword_service(get_settings(), session, context)
    members = await service.list_members(cluster_id, limit)
    return KeywordMemberCollection(
        data=members, meta={"trace_id": context.trace_id, "count": len(members)}
    )
