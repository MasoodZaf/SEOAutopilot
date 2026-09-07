import hmac
import secrets
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.context import Role, TenantContext
from app.core.oidc import (
    OidcConfigurationError,
    OidcVerificationError,
    OidcVerifier,
    discover_jwks_uri,
)

bearer = HTTPBearer(auto_error=False)

_verifier: OidcVerifier | None = None


async def get_oidc_verifier(settings: Settings) -> OidcVerifier | None:
    """Build the verifier once, discovering the provider's keys if not pinned.

    Discovery is a network call, so it happens on the first request that needs
    it rather than at import time, where a provider being briefly unreachable
    would stop the process from starting at all.
    """
    global _verifier
    if _verifier is not None:
        return _verifier
    if not settings.oidc_issuer_url or not settings.oidc_audience:
        return None
    jwks_uri = settings.oidc_jwks_uri
    if not jwks_uri:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(10.0)
        ) as client:
            jwks_uri = await discover_jwks_uri(settings.oidc_issuer_url, client)
    _verifier = OidcVerifier(
        issuer=settings.oidc_issuer_url,
        audience=settings.oidc_audience,
        jwks_uri=jwks_uri,
        cache_seconds=settings.oidc_jwks_cache_seconds,
    )
    return _verifier


def reset_oidc_verifier() -> None:
    """Drop the cached verifier. Tests and configuration reloads only."""
    global _verifier
    _verifier = None


def resolve_local_pilot_context(
    settings: Settings, credentials: HTTPAuthorizationCredentials | None
) -> TenantContext | None:
    """Resolve one of the pilot's named operators, or nobody.

    Two tokens, two actor ids. Separation of duties was enforceable but never
    exercisable with a single identity: the author of a proposal cannot approve
    it, so a medium-risk change could be refused forever and approved never.
    Each token is compared in full so that failing to match the first does not
    reveal anything about the second.

    This is development only, and now genuinely optional rather than the only
    way in. It survives because a local stack should not need an identity
    provider to run the test suite or drive the UI.
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


def _requested_tenant(raw: str | None) -> UUID | None:
    """Which tenant the caller wants to act on, if they said.

    A selector, not a grant. The resolver only ever matches it against the
    memberships the caller already has, so a value here can narrow the answer
    and never widen it. An unparseable one is refused rather than ignored,
    because silently acting on a different tenant is the failure worth avoiding.
    """
    if raw is None or not raw.strip():
        return None
    try:
        return UUID(raw.strip())
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="tenant_selector_invalid"
        ) from error


async def require_tenant_context(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    tenant_selector: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> TenantContext:
    """Establish who is calling and whose data they may act on.

    Tenant authority is never accepted from a public header or request body: the
    header below selects among memberships that already exist, and the
    membership is what grants anything.
    """
    local_context = resolve_local_pilot_context(settings, credentials)
    if local_context is not None:
        return local_context

    try:
        verifier = await get_oidc_verifier(settings)
    except (OidcConfigurationError, httpx.HTTPError) as error:
        # The provider is unreachable or misconfigured. That is an outage on
        # this side, and reporting it as 401 would send every user to log in
        # again to fix something they cannot.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication_unavailable",
        ) from error
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_not_configured"
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    try:
        identity = verifier.verify(credentials.credentials)
    except OidcVerificationError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error

    # Imported here rather than at module scope: the session module depends on
    # this one for the tenant-scoped session, so importing it back at import
    # time would be a cycle.
    from app.db.session import authenticating_session
    from app.services.identity import IdentityResolver

    async with authenticating_session() as session:
        return await IdentityResolver(session).resolve(
            identity,
            requested_tenant_id=_requested_tenant(tenant_selector),
            trace_id=f"req-{secrets.token_hex(12)}",
        )


TenantContextDependency = Annotated[TenantContext, Depends(require_tenant_context)]
