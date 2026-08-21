from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter(tags=["system"])


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/system/readiness")
async def readiness(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    return {
        "data": {
            "status": "foundation",
            "deployments_enabled": settings.deployments_enabled,
            "autopilot_enabled": settings.autopilot_enabled,
            "authentication_configured": bool(settings.oidc_issuer_url),
        }
    }
