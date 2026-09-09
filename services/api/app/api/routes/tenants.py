"""Workspaces, and the provider credentials a workspace runs on.

Split across two dependencies on purpose. The first two routes take
`ActorContextDependency`, which proves a person and establishes no tenant scope
at all -- they have to work for somebody who has just signed in and is in
nothing. Everything below them takes `TenantContextDependency` and a tenant
scoped session, like the rest of the API.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.schemas import (
    GitHubAppCreate,
    GoogleOAuthClientCreate,
    TenantCreate,
    TenantCredentialCollection,
    TenantCredentialRead,
    TenantEnvelope,
    TenantMembershipCollection,
    TenantMembershipRead,
    TenantRead,
)
from app.core.auth import ActorContextDependency, TenantContextDependency
from app.core.config import get_settings
from app.db.models import Tenant, TenantMembership
from app.db.session import TenantSession, authenticating_session
from app.services.tenant_credentials import (
    GITHUB_APP,
    GOOGLE_OAUTH_CLIENT,
    SUPPORTED_PROVIDERS,
    TenantCredentialService,
    store_for,
)
from app.services.tenant_provisioning import TenantProvisioningService

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])
credentials_router = APIRouter(prefix="/v1/tenant/credentials", tags=["tenants"])


@router.get("", response_model=TenantMembershipCollection)
async def list_my_tenants(actor: ActorContextDependency) -> TenantMembershipCollection:
    """Which workspaces the caller is in.

    Answers honestly with an empty list rather than the 403 the tenant-scoped
    dependency would raise, because "you are in none yet" is the ordinary state
    of somebody who has just signed in, and the UI needs to tell them so.
    """
    async with authenticating_session() as session:
        rows = await session.execute(
            select(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == actor.actor_id,
                TenantMembership.status == "active",
            )
            .order_by(TenantMembership.created_at)
        )
        data = [
            TenantMembershipRead(
                tenant_id=tenant.id, slug=tenant.slug, name=tenant.name, role=membership.role
            )
            for membership, tenant in rows.all()
        ]
    return TenantMembershipCollection(
        data=data, meta={"trace_id": actor.trace_id, "count": len(data)}
    )


@router.post("", response_model=TenantEnvelope, status_code=status.HTTP_201_CREATED)
async def create_tenant(command: TenantCreate, actor: ActorContextDependency) -> TenantEnvelope:
    """Create a workspace and become its owner.

    Safe to expose where an invitation route would not be: this makes an empty
    tenant and grants its creator authority over nothing but that. It reaches
    into no existing tenant's data, which is the property that made invitations
    member-only.
    """
    async with authenticating_session() as session:
        tenant = await TenantProvisioningService(session).create(actor, command.name)
        data = TenantRead(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status,
            created_at=tenant.created_at or datetime.now().astimezone(),
        )
    return TenantEnvelope(data=data, meta={"trace_id": actor.trace_id})


@credentials_router.get("", response_model=TenantCredentialCollection)
async def list_credentials(
    context: TenantContextDependency, session: TenantSession
) -> TenantCredentialCollection:
    """What this tenant has configured, and whose credential is actually in use.

    `source` is the value worth reading: "tenant" means a consent screen names
    their own application, "platform" means it still names the deployment's and
    spends the deployment's quota. A tenant that thinks it is isolated and is
    quietly using ours should be able to see that in one glance.
    """
    settings = get_settings()
    store = store_for(session, settings)
    data: list[TenantCredentialRead] = []
    for provider, platform_configured in (
        (GOOGLE_OAUTH_CLIENT, bool(settings.google_client_id and settings.google_client_secret)),
        (GITHUB_APP, settings.github_app_configured),
    ):
        credential = await store.describe(context.tenant_id, provider)
        if credential is not None:
            data.append(
                TenantCredentialRead(
                    provider=provider,
                    source="tenant",
                    config=dict(credential.config_json),
                    configured_at=credential.created_at,
                )
            )
        elif platform_configured:
            data.append(
                TenantCredentialRead(
                    provider=provider, source="platform", config={}, configured_at=None
                )
            )
        else:
            data.append(
                TenantCredentialRead(
                    provider=provider, source="none", config={}, configured_at=None
                )
            )
    return TenantCredentialCollection(
        data=data,
        callbacks={
            "google_oauth_client": settings.google_oauth_redirect_uri,
            "github_app": settings.github_app_callback_url,
        },
        meta={"trace_id": context.trace_id, "count": len(data)},
    )


@credentials_router.put(
    "/google_oauth_client",
    response_model=TenantCredentialRead,
    status_code=status.HTTP_201_CREATED,
)
async def put_google_client(
    command: GoogleOAuthClientCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> TenantCredentialRead:
    settings = get_settings()
    store = store_for(session, settings)
    credential = await TenantCredentialService(session, context).upsert_google_client(
        store, command.client_id, command.client_secret.get_secret_value()
    )
    return TenantCredentialRead(
        provider=GOOGLE_OAUTH_CLIENT,
        source="tenant",
        config=dict(credential.config_json),
        configured_at=credential.created_at,
    )


@credentials_router.put(
    "/github_app", response_model=TenantCredentialRead, status_code=status.HTTP_201_CREATED
)
async def put_github_app(
    command: GitHubAppCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> TenantCredentialRead:
    settings = get_settings()
    store = store_for(session, settings)
    credential = await TenantCredentialService(session, context).upsert_github_app(
        store, command.app_id, command.app_slug, command.private_key.get_secret_value()
    )
    return TenantCredentialRead(
        provider=GITHUB_APP,
        source="tenant",
        config=dict(credential.config_json),
        configured_at=credential.created_at,
    )


@credentials_router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_credential(
    provider: str, context: TenantContextDependency, session: TenantSession
) -> Response:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="tenant_credential_not_found"
        )
    settings = get_settings()
    store = store_for(session, settings)
    removed = await TenantCredentialService(session, context).revoke(store, provider)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="tenant_credential_not_found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
