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
    """Resolve one of the pilot's named operators, or nobody.

    Two tokens, two actor ids. Separation of duties was enforceable but never
    exercisable with a single identity: the author of a proposal cannot approve
    it, so a medium-risk change could be refused forever and approved never.
    Each token is compared in full so that failing to match the first does not
    reveal anything about the second.
    """
    if (
        not settings.local_pilot_auth_enabled
        or settings.app_env != "development"
        or not settings.local_pilot_auth_token
        or credentials is None
        or credentials.scheme.lower() != "bearer"
    ):
        return None

    presented = credentials.credentials
    operators = [(settings.local_pilot_auth_token, settings.local_pilot_actor_id)]
    if settings.local_pilot_reviewer_token is not None:
        operators.append((settings.local_pilot_reviewer_token, settings.local_pilot_reviewer_id))

    actor_id = None
    for secret, candidate in operators:
        # No early exit: every token is compared on every request, so timing
        # cannot say which operator a near-miss was close to.
        if hmac.compare_digest(presented, secret.get_secret_value()):
            actor_id = candidate
    if actor_id is None:
        return None

    return TenantContext(
        tenant_id=settings.local_pilot_tenant_id,
        actor_id=actor_id,
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
