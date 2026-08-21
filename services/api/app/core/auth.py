import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.context import Role, TenantContext

bearer = HTTPBearer(auto_error=False)


def resolve_local_pilot_context(
    settings: Settings, credentials: HTTPAuthorizationCredentials | None
) -> TenantContext | None:
    if (
        not settings.local_pilot_auth_enabled
        or settings.app_env != "development"
        or not settings.local_pilot_auth_token
        or credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(
            credentials.credentials,
            settings.local_pilot_auth_token.get_secret_value(),
        )
    ):
        return None
    return TenantContext(
        tenant_id=settings.local_pilot_tenant_id,
        actor_id=settings.local_pilot_actor_id,
        role=Role.OWNER,
        trace_id=f"local-pilot-{secrets.token_hex(12)}",
    )


async def require_tenant_context(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> TenantContext:
    """Fail closed until the OIDC verifier establishes a trusted context.

    Tests override this dependency directly. Tenant authority is never accepted
    from a public header or request body.
    """
    local_context = resolve_local_pilot_context(settings, credentials)
    if local_context is not None:
        return local_context
    detail = "authentication_not_configured" if not settings.oidc_issuer_url else "unauthorized"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


TenantContextDependency = Annotated[TenantContext, Depends(require_tenant_context)]
