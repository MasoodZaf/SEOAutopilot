import secrets
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.api.schemas import (
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
)
from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.db.session import SystemSession, TenantSession
from app.services.cloudflare_dns import CloudflareDnsHttpClient
from app.services.connector_secrets import DatabaseEnvelopeSecretStore, decode_encryption_key
from app.services.connectors import ConnectorOAuthCallbackService, ConnectorService
from app.services.google_oauth import GoogleOAuthHttpClient

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
        or not settings.google_client_id
        or not settings.google_client_secret
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
        provider = GoogleOAuthHttpClient(
            http_client,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            redirect_uri=settings.google_oauth_redirect_uri,
        )
        await ConnectorOAuthCallbackService(session, provider, secret_store).complete_gsc(
            state, code, secrets.token_hex(16)
        )
    return RedirectResponse(
        url=f"{settings.app_base_url.rstrip('/')}/settings/connectors?google=connected",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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
