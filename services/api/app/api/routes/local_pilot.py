from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.db.models import Tenant
from app.db.session import SystemSession

router = APIRouter(prefix="/v1/local-pilot", tags=["local-pilot"])


class LocalPilotSession(BaseModel):
    tenant_id: str
    mode: str


class LocalPilotEnvelope(BaseModel):
    data: LocalPilotSession
    meta: dict[str, str]


@router.post("/bootstrap", response_model=LocalPilotEnvelope, status_code=status.HTTP_201_CREATED)
async def bootstrap_local_pilot(
    context: TenantContextDependency, session: SystemSession
) -> LocalPilotEnvelope:
    settings = get_settings()
    if settings.app_env != "development" or not settings.local_pilot_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    tenant = await session.get(Tenant, context.tenant_id)
    if tenant is None:
        tenant = Tenant(
            id=context.tenant_id,
            slug="codearc-pilot",
            name="CodeArc Pilot",
            status="active",
        )
        session.add(tenant)
        await session.flush()
    return LocalPilotEnvelope(
        data=LocalPilotSession(tenant_id=str(tenant.id), mode="observe"),
        meta={"trace_id": context.trace_id},
    )
