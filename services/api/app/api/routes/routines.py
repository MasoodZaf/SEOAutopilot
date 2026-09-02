from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    NotificationChannelCollection,
    NotificationChannelCreate,
    NotificationChannelEnvelope,
    NotificationChannelRead,
    ReportCollection,
    ReportEnvelope,
    ReportKindName,
    ReportRead,
    ReportSummary,
    RoutineCollection,
    RoutineEnvelope,
    RoutineRead,
    RoutineRunCollection,
    RoutineRunEnvelope,
    RoutineRunRead,
    RoutineUpsert,
)
from app.core.auth import TenantContextDependency
from app.core.config import Settings, get_settings
from app.db.session import TenantSession
from app.services.connector_secrets import decode_encryption_key
from app.services.notifications import NotificationChannelService
from app.services.reports import ReportService
from app.services.routines import RoutineService

router = APIRouter(prefix="/v1", tags=["routines"])


def require_routines_enabled(settings: Settings) -> None:
    if not settings.routines_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="routines_not_enabled"
        )


def notification_service(
    settings: Settings, session: TenantSession, context: TenantContextDependency
) -> NotificationChannelService:
    if (
        not settings.notifications_enabled
        or settings.connector_secret_backend != "database_envelope"
        or not settings.connector_secret_encryption_key
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="notifications_not_configured",
        )
    return NotificationChannelService(
        session,
        context,
        encryption_key=decode_encryption_key(
            settings.connector_secret_encryption_key.get_secret_value()
        ),
        key_version=settings.connector_secret_key_version,
    )


@router.get("/sites/{site_id}/routines", response_model=RoutineCollection)
async def list_routines(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> RoutineCollection:
    require_routines_enabled(get_settings())
    routines = await RoutineService(session, context).list_for_site(site_id)
    if routines is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return RoutineCollection(
        data=[RoutineRead.model_validate(item) for item in routines],
        meta={"trace_id": context.trace_id, "count": len(routines)},
    )


@router.put("/sites/{site_id}/routines", response_model=RoutineEnvelope)
async def upsert_routine(
    site_id: UUID,
    command: RoutineUpsert,
    context: TenantContextDependency,
    session: TenantSession,
) -> RoutineEnvelope:
    require_routines_enabled(get_settings())
    routine = await RoutineService(session, context).upsert(site_id, command)
    return RoutineEnvelope(
        data=RoutineRead.model_validate(routine), meta={"trace_id": context.trace_id}
    )


@router.post(
    "/routines/{routine_id}/runs",
    response_model=RoutineRunEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_routine(
    routine_id: UUID, context: TenantContextDependency, session: TenantSession
) -> RoutineRunEnvelope:
    require_routines_enabled(get_settings())
    run = await RoutineService(session, context).trigger(routine_id)
    return RoutineRunEnvelope(
        data=RoutineRunRead.model_validate(run), meta={"trace_id": context.trace_id}
    )


@router.get("/sites/{site_id}/routine-runs", response_model=RoutineRunCollection)
async def list_routine_runs(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=25, ge=1, le=100),
) -> RoutineRunCollection:
    require_routines_enabled(get_settings())
    runs = await RoutineService(session, context).list_runs(site_id, limit)
    if runs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return RoutineRunCollection(
        data=[RoutineRunRead.model_validate(item) for item in runs],
        meta={"trace_id": context.trace_id, "count": len(runs)},
    )


@router.get("/sites/{site_id}/reports", response_model=ReportCollection)
async def list_reports(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=25, ge=1, le=100),
    kind: ReportKindName | None = None,
) -> ReportCollection:
    reports = await ReportService(session, context).list_for_site(
        site_id, limit, kind.value if kind else None
    )
    if reports is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return ReportCollection(
        data=[ReportSummary.model_validate(item) for item in reports],
        meta={"trace_id": context.trace_id, "count": len(reports)},
    )


@router.get("/reports/{report_id}", response_model=ReportEnvelope)
async def get_report(
    report_id: UUID, context: TenantContextDependency, session: TenantSession
) -> ReportEnvelope:
    report = await ReportService(session, context).get(report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report_not_found")
    return ReportEnvelope(
        data=ReportRead.model_validate(report), meta={"trace_id": context.trace_id}
    )


@router.get("/notification-channels", response_model=NotificationChannelCollection)
async def list_notification_channels(
    context: TenantContextDependency, session: TenantSession
) -> NotificationChannelCollection:
    service = notification_service(get_settings(), session, context)
    channels = await service.list_channels()
    return NotificationChannelCollection(
        data=[NotificationChannelRead.model_validate(item) for item in channels],
        meta={"trace_id": context.trace_id, "count": len(channels)},
    )


@router.post(
    "/notification-channels",
    response_model=NotificationChannelEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_channel(
    command: NotificationChannelCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> NotificationChannelEnvelope:
    service = notification_service(get_settings(), session, context)
    channel = await service.create(command)
    return NotificationChannelEnvelope(
        data=NotificationChannelRead.model_validate(channel), meta={"trace_id": context.trace_id}
    )


@router.post(
    "/notification-channels/{channel_id}/revoke",
    response_model=NotificationChannelEnvelope,
)
async def revoke_notification_channel(
    channel_id: UUID, context: TenantContextDependency, session: TenantSession
) -> NotificationChannelEnvelope:
    service = notification_service(get_settings(), session, context)
    channel = await service.revoke(channel_id)
    return NotificationChannelEnvelope(
        data=NotificationChannelRead.model_validate(channel), meta={"trace_id": context.trace_id}
    )
