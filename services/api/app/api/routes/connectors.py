import secrets
from datetime import datetime, timedelta
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.api.schemas import (
    AnalyticsAuthorizationCreate,
    ConnectionCollection,
    ConnectionRead,
    ConnectorAuthorizationCreate,
    ConnectorAuthorizationEnvelope,
    ConnectorAuthorizationRead,
    ConnectorCollection,
    ConnectorRead,
    ConnectorSyncCreate,
    ConnectorSyncEnvelope,
    ConnectorSyncRead,
    DnsProviderConnectorCreate,
    DnsProviderVerificationCreate,
    DnsProviderVerificationEnvelope,
    DnsProviderVerificationRead,
    GitHubConnectorCreate,
    GitHubInstallationEnvelope,
    GitHubInstallationRead,
    GitHubRepositoryTarget,
    GoogleAuthorizationEnvelope,
    GoogleAuthorizationRead,
)
from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.db.session import SystemSession, TenantSession
from app.services.cloudflare_dns import CloudflareDnsHttpClient
from app.services.connector_secrets import DatabaseEnvelopeSecretStore, decode_encryption_key
from app.services.connectors import ConnectorOAuthCallbackService, ConnectorService
from app.services.github_app import GitHubAppClient
from app.services.github_connector import (
    GitHubConnectorService,
    GitHubInstallationCallbackService,
    GitHubRepositoryHttpProbe,
)
from app.services.google_analytics import AnalyticsAdminHttpClient
from app.services.google_oauth import GoogleOAuthHttpClient
from app.services.tenant_credentials import github_app_credential, google_oauth_client

router = APIRouter(prefix="/v1", tags=["connectors"])


def local_dns_provider_secret_store(settings, session: TenantSession) -> DatabaseEnvelopeSecretStore:
    if (
        not settings.dns_provider_connectors_enabled
        or settings.connector_secret_backend != "database_envelope"
        or not settings.connector_secret_encryption_key
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dns_provider_connector_not_configured",
        )
    return DatabaseEnvelopeSecretStore(
        session,
        decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value()),
        settings.connector_secret_key_version,
    )


def local_secret_store(settings, session, detail: str) -> DatabaseEnvelopeSecretStore:
    """The envelope store, or a refusal that names the connector asking for it."""
    if (
        settings.connector_secret_backend != "database_envelope"
        or not settings.connector_secret_encryption_key
    ):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return DatabaseEnvelopeSecretStore(
        session,
        decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value()),
        settings.connector_secret_key_version,
    )


@router.get("/sites/{site_id}/connectors", response_model=ConnectorCollection)
async def list_connectors(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> ConnectorCollection:
    connectors = await ConnectorService(session, context).list_for_site(site_id)
    if connectors is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return ConnectorCollection(
        data=[ConnectorRead.model_validate(item) for item in connectors],
        meta={"trace_id": context.trace_id, "count": len(connectors)},
    )


GOOGLE_CONNECTOR_TYPES = frozenset({"google_search_console", "google_analytics"})


def grant_expires_at(connector, lifetime_days: int | None) -> datetime | None:
    """When a person will next have to consent, if the provider imposes a date.

    Only Google grants have one, and only while the consent screen is in
    Testing -- which the operator declares, because nothing Google returns
    says so. A connector that is not holding a grant has nothing to expire.
    """
    if (
        lifetime_days is None
        or connector.type not in GOOGLE_CONNECTOR_TYPES
        or connector.consented_at is None
        or connector.status not in {"active", "reauthorization_required"}
    ):
        return None
    return connector.consented_at + timedelta(days=lifetime_days)


@router.get("/connections", response_model=ConnectionCollection)
async def list_connections(
    context: TenantContextDependency, session: TenantSession
) -> ConnectionCollection:
    """Every connector in the workspace, for the connection manager and banner."""
    lifetime = get_settings().google_grant_lifetime_days
    rows = await ConnectorService(session, context).list_for_tenant()
    data = [
        ConnectionRead.model_validate(
            {
                **ConnectorRead.model_validate(connector).model_dump(),
                "site_name": site.name,
                "site_host": site.normalized_host,
                "grant_expires_at": grant_expires_at(connector, lifetime),
            }
        )
        for connector, site in rows
    ]
    needs_attention = sum(
        1 for item in data if item.status in {"reauthorization_required", "error"}
    )
    return ConnectionCollection(
        data=data,
        meta={
            "trace_id": context.trace_id,
            "count": len(data),
            "needs_attention": needs_attention,
        },
    )


@router.post(
    "/connectors/google/authorize",
    response_model=GoogleAuthorizationEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def authorize_google(
    context: TenantContextDependency, session: TenantSession
) -> GoogleAuthorizationEnvelope:
    """One consent for Search Console and GA4, bound to every verified site."""
    authorization_url, expires_at = await ConnectorService(
        session, context
    ).begin_google_authorization(get_settings())
    return GoogleAuthorizationEnvelope(
        data=GoogleAuthorizationRead(authorization_url=authorization_url, expires_at=expires_at),
        meta={"trace_id": context.trace_id},
    )


@router.post("/connectors/{connector_id}/disconnect", response_model=ConnectorRead)
async def disconnect_connector(
    connector_id: UUID, context: TenantContextDependency, session: TenantSession
) -> ConnectorRead:
    connector = await ConnectorService(session, context).disconnect(connector_id)
    return ConnectorRead.model_validate(connector)


@router.post(
    "/sites/{site_id}/connectors/google_search_console/authorize",
    response_model=ConnectorAuthorizationEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def authorize_gsc(
    site_id: UUID,
    command: ConnectorAuthorizationCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ConnectorAuthorizationEnvelope:
    connector, authorization_url, expires_at = await ConnectorService(
        session, context
    ).begin_gsc_authorization(site_id, command.property_ref, get_settings())
    return ConnectorAuthorizationEnvelope(
        data=ConnectorAuthorizationRead(
            connector=ConnectorRead.model_validate(connector),
            authorization_url=authorization_url,
            expires_at=expires_at,
        ),
        meta={"trace_id": context.trace_id},
    )


@router.post(
    "/sites/{site_id}/connectors/google_analytics/authorize",
    response_model=ConnectorAuthorizationEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def authorize_analytics(
    site_id: UUID,
    command: AnalyticsAuthorizationCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ConnectorAuthorizationEnvelope:
    connector, authorization_url, expires_at = await ConnectorService(
        session, context
    ).begin_analytics_authorization(site_id, command.property_ref, get_settings())
    return ConnectorAuthorizationEnvelope(
        data=ConnectorAuthorizationRead(
            connector=ConnectorRead.model_validate(connector),
            authorization_url=authorization_url,
            expires_at=expires_at,
        ),
        meta={"trace_id": context.trace_id},
    )


def dns_provider_client(provider_key: str, http_client: httpx.AsyncClient) -> CloudflareDnsHttpClient:
    if provider_key != "cloudflare":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="dns_provider_not_supported"
        )
    return CloudflareDnsHttpClient(http_client)


@router.post(
    "/sites/{site_id}/dns-connectors/{provider_key}",
    response_model=ConnectorRead,
    status_code=status.HTTP_201_CREATED,
)
async def connect_dns_provider(
    site_id: UUID,
    provider_key: str,
    command: DnsProviderConnectorCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ConnectorRead:
    settings = get_settings()
    secret_store = local_dns_provider_secret_store(settings, session)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http_client:
        connector = await ConnectorService(session, context).connect_dns_provider(
            site_id,
            provider_key,
            command.zone_id,
            command.api_token.get_secret_value(),
            dns_provider_client(provider_key, http_client),
            secret_store,
        )
    return ConnectorRead.model_validate(connector)


@router.post(
    "/sites/{site_id}/dns-connectors/{provider_key}/verification",
    response_model=DnsProviderVerificationEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_dns_provider_verification(
    site_id: UUID,
    provider_key: str,
    command: DnsProviderVerificationCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> DnsProviderVerificationEnvelope:
    settings = get_settings()
    secret_store = local_dns_provider_secret_store(settings, session)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http_client:
        record_id, record_name = await ConnectorService(session, context).publish_dns_provider_challenge(
            site_id,
            provider_key,
            command.token.get_secret_value(),
            dns_provider_client(provider_key, http_client),
            secret_store,
        )
    return DnsProviderVerificationEnvelope(
        data=DnsProviderVerificationRead(record_id=record_id, record_name=record_name),
        meta={"trace_id": context.trace_id},
    )


@router.get("/connectors/oauth/callback", include_in_schema=True)
async def google_oauth_callback(
    session: SystemSession,
    state: str = Query(min_length=32, max_length=256),
    code: str = Query(min_length=1, max_length=4096),
) -> RedirectResponse:
    settings = get_settings()
    if (
        not settings.google_connectors_enabled
        or settings.connector_secret_backend != "database_envelope"
        or not settings.connector_secret_encryption_key
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="google_connector_callback_not_configured",
        )
    secret_store = DatabaseEnvelopeSecretStore(
        session,
        decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value()),
        settings.connector_secret_key_version,
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http_client:

        async def provider_for(tenant_id: UUID) -> GoogleOAuthHttpClient:
            """The client that started this consent, resolved once we know whose.

            A code is redeemable only by the client id it was issued to, so
            exchanging with the deployment's client a code that a tenant's own
            client issued fails at Google with `invalid_client`. Which one it
            was is not knowable until the state row names the tenant, which is
            why this is a factory and not a value.
            """
            client = await google_oauth_client(session, settings, tenant_id)
            if client is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="google_oauth_client_not_configured",
                )
            return GoogleOAuthHttpClient(
                http_client,
                client_id=client.client_id,
                client_secret=client.client_secret,
                redirect_uri=settings.google_oauth_redirect_uri,
            )

        callback = ConnectorOAuthCallbackService(
            session,
            None,
            secret_store,
            AnalyticsAdminHttpClient(http_client),
            provider_factory=provider_for,
        )
        try:
            await callback.complete_authorization(state, code, secrets.token_hex(16))
        except HTTPException as refusal:
            # A person is at the end of this redirect, not a client library.
            #
            # Raising here renders `{"detail":"search_console_property_not_authorized"}`
            # as a bare JSON document at an api/v1 URL, which is where a real
            # connection attempt ended on 2026-09-08: a correct, specific,
            # actionable refusal, shown in a form that offers no way to act on
            # it and no way back to the page that started the flow.
            #
            # The settings page already knows how to say what each of these
            # means, so the refusal is handed to it. The status code is not
            # lost -- it was never seen by anything that reads status codes.
            # Only a refusal is redirected; an unexpected failure still raises,
            # because turning a 500 into a tidy error message on a page is how
            # a broken deployment comes to look merely unlucky.
            detail = refusal.detail if isinstance(refusal.detail, str) else "connector_refused"
            return RedirectResponse(
                url=(
                    f"{settings.app_base_url.rstrip('/')}/settings/connectors"
                    f"?error={quote(detail, safe='')}"
                ),
                status_code=status.HTTP_303_SEE_OTHER,
            )
    target = f"{settings.app_base_url.rstrip('/')}/settings/connectors?google=connected"
    if callback.outcome is not None:
        # Counts only: which site got which property is on the page itself.
        target += f"&linked={len(callback.outcome.linked)}"
        target += f"&unmatched={len(callback.outcome.unmatched)}"
        if len(callback.outcome.granted_scopes) < 2:
            target += "&partial=1"
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/connectors/{connector_id}/syncs",
    response_model=ConnectorSyncEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_connector_sync(
    connector_id: UUID,
    command: ConnectorSyncCreate,
    context: TenantContextDependency,
    session: TenantSession,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> ConnectorSyncEnvelope:
    sync = await ConnectorService(session, context).create_sync(
        connector_id, command, idempotency_key
    )
    return ConnectorSyncEnvelope(
        data=ConnectorSyncRead.model_validate(sync), meta={"trace_id": context.trace_id}
    )


@router.post(
    "/sites/{site_id}/connectors/github/installation",
    response_model=GitHubInstallationEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def begin_github_installation(
    site_id: UUID,
    command: GitHubRepositoryTarget,
    context: TenantContextDependency,
    session: TenantSession,
) -> GitHubInstallationEnvelope:
    """Send the tenant to GitHub to install the app on their repository.

    Nothing is stored until GitHub sends them back, and no credential passes
    through this service at any point: the tenant grants the app, and the token
    is minted per deployment and never written down.
    """
    connector, installation_url, expires_at = await GitHubConnectorService(
        session, context
    ).begin_app_installation(
        site_id,
        command.repository,
        command.base_branch,
        command.path_template,
        get_settings(),
    )
    return GitHubInstallationEnvelope(
        data=GitHubInstallationRead(
            connector=ConnectorRead.model_validate(connector),
            installation_url=installation_url,
            expires_at=expires_at,
        ),
        meta={"trace_id": context.trace_id},
    )


@router.get("/connectors/github/callback", include_in_schema=True)
async def github_installation_callback(
    session: SystemSession,
    state: str = Query(min_length=32, max_length=256),
    installation_id: int = Query(gt=0),
    setup_action: str = Query(default="install", max_length=32),
) -> RedirectResponse:
    settings = get_settings()
    if not settings.github_connectors_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="github_connector_callback_not_configured",
        )
    page = f"{settings.app_base_url.rstrip('/')}/settings/connectors"
    if setup_action not in {"install", "update"}:
        # A cancelled install returns here too. There is nothing to record,
        # and the person belongs back on the page they started from.
        return RedirectResponse(
            url=f"{page}?error=installation_not_completed",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=httpx.Timeout(30.0)
    ) as http_client:

        async def app_client_for(tenant_id: UUID) -> GitHubAppClient:
            app = await github_app_credential(session, settings, tenant_id)
            if app is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="github_app_not_configured",
                )
            return GitHubAppClient(http_client, app.app_id, app.private_key)

        try:
            await GitHubInstallationCallbackService(
                session, app_client_factory=app_client_for
            ).complete(state, installation_id, secrets.token_hex(16))
        except HTTPException as refusal:
            # Same reasoning as the Google callback: a person is at the end of
            # this redirect, and a JSON body at an API URL gives them no way
            # back. Only refusals are redirected; a 500 still raises.
            detail = refusal.detail if isinstance(refusal.detail, str) else "connector_refused"
            return RedirectResponse(
                url=f"{page}?error={quote(detail, safe='')}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    return RedirectResponse(url=f"{page}?github=connected", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/sites/{site_id}/connectors/github/token",
    response_model=ConnectorRead,
    status_code=status.HTTP_201_CREATED,
)
async def connect_github_token(
    site_id: UUID,
    command: GitHubConnectorCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> ConnectorRead:
    """Bind a tenant-supplied token to one site.

    The app installation above is the better path and should be offered first:
    this one stores a long-lived credential that belongs to a person. It exists
    so an install already running on a shared token can move to a per-site
    connector without waiting for an app registration.
    """
    settings = get_settings()
    if not settings.github_connectors_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="github_connector_not_configured",
        )
    secret_store = local_secret_store(settings, session, "github_connector_not_configured")
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=httpx.Timeout(15.0)
    ) as http_client:
        connector = await GitHubConnectorService(session, context).connect_personal_access_token(
            site_id,
            command.repository,
            command.base_branch,
            command.path_template,
            command.access_token.get_secret_value(),
            GitHubRepositoryHttpProbe(http_client),
            secret_store,
        )
    return ConnectorRead.model_validate(connector)
