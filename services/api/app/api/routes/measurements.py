from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    MeasurementCollection,
    MeasurementSeriesEnvelope,
    MeasurementSeriesRead,
    PostDeployVerificationEnvelope,
    PostDeployVerificationRead,
)
from app.core.auth import TenantContextDependency
from app.db.session import TenantSession
from app.services.measurements import MeasurementService

router = APIRouter(tags=["measurements"])


@router.post(
    "/v1/proposals/{proposal_id}/verify",
    response_model=PostDeployVerificationEnvelope,
    status_code=status.HTTP_200_OK,
)
async def verify_proposal_deployment(
    proposal_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> PostDeployVerificationEnvelope:
    verification = await MeasurementService(session, context).verify_deployment(proposal_id)
    return PostDeployVerificationEnvelope(
        data=PostDeployVerificationRead.model_validate(verification),
        meta={"trace_id": context.trace_id},
    )


@router.get(
    "/v1/proposals/{proposal_id}/verification",
    response_model=PostDeployVerificationEnvelope,
)
async def get_proposal_verification(
    proposal_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> PostDeployVerificationEnvelope:
    verification = await MeasurementService(session, context).get_verification(proposal_id)
    if verification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="verification_not_found")
    return PostDeployVerificationEnvelope(
        data=PostDeployVerificationRead.model_validate(verification),
        meta={"trace_id": context.trace_id},
    )


@router.get(
    "/v1/proposals/{proposal_id}/measurement",
    response_model=MeasurementSeriesEnvelope,
)
async def get_proposal_measurement(
    proposal_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
) -> MeasurementSeriesEnvelope:
    series = await MeasurementService(session, context).calculate_or_get_measurement(proposal_id)
    return MeasurementSeriesEnvelope(
        data=MeasurementSeriesRead.model_validate(series),
        meta={"trace_id": context.trace_id},
    )


@router.get(
    "/v1/sites/{site_id}/measurements",
    response_model=MeasurementCollection,
)
async def list_site_measurements(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=50, ge=1, le=100),
) -> MeasurementCollection:
    measurements = await MeasurementService(session, context).list_site_measurements(
        site_id=site_id,
        limit=limit,
    )
    return MeasurementCollection(
        data=[MeasurementSeriesRead.model_validate(m) for m in measurements],
        meta={"trace_id": context.trace_id, "count": len(measurements)},
    )
