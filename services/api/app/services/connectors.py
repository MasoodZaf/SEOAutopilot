import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ConnectorSyncCreate
from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    Connector,
    ConnectorOauthState,
    ConnectorSync,
    OutboxEvent,
    Site,
    SiteVerificationChallenge,
)
from app.services.connector_secrets import ConnectorSecretReader, ConnectorSecretStore
from app.services.dns_provider import DnsProvider, DnsProviderError
from app.services.google_oauth import GoogleOAuthError, GoogleOAuthProvider
from app.services.sites import SiteService, stable_hash

GSC_CONNECTOR = "google_search_console"
DNS_PROVIDER_CONNECTOR = "dns_provider"
GSC_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
MAX_BACKFILL_DAYS = 490
GSC_PERMITTED_LEVELS = frozenset({"siteOwner", "siteFullUser", "siteRestrictedUser"})


def property_matches_site(property_ref: str, normalized_host: str) -> bool:
    """Bind a returned Search Console property to the already verified site."""
    expected = normalized_host.rstrip(".").lower()
    if property_ref.startswith("sc-domain:"):
        domain = property_ref.removeprefix("sc-domain:").rstrip(".").lower()
        return bool(domain) and (expected == domain or expected.endswith(f".{domain}"))
    parsed = urlsplit(property_ref)
    return parsed.scheme in {"http", "https"} and parsed.hostname == expected


class ConnectorService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.site_service = SiteService(session, context)

    async def list_for_site(self, site_id: UUID) -> list[Connector] | None:
        if await self.site_service.get_site(site_id) is None:
            return None
        result = await self.session.scalars(
            select(Connector)
            .where(Connector.tenant_id == self.context.tenant_id, Connector.site_id == site_id)
            .order_by(Connector.created_at, Connector.id)
        )
        return list(result)

    async def begin_gsc_authorization(
        self, site_id: UUID, property_ref: str, settings: Settings
    ) -> tuple[Connector, str, datetime]:
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._verified_site(site_id)
        if not settings.google_connectors_enabled or not settings.google_client_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="google_connector_not_configured",
            )
        if not property_matches_site(property_ref, site.normalized_host):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="property_site_mismatch",
            )
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == site.id,
                Connector.type == GSC_CONNECTOR,
            )
        )
        if connector is None:
            connector = Connector(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                type=GSC_CONNECTOR,
                status="pending_authorization",
            )
            self.session.add(connector)
            await self.session.flush()
        elif connector.status == "active":
            connector.status = "reauthorization_required"
            connector.version += 1

        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        self.session.add(
            ConnectorOauthState(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                connector_id=connector.id,
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                requested_scopes=[GSC_READONLY_SCOPE],
                requested_property_ref=property_ref,
                expires_at=expires_at,
                created_by=self.context.actor_id,
            )
        )
        self.site_service._stage_event(
            "connector.authorization_started",
            "connector",
            connector.id,
            {"site_id": str(site.id), "connector_type": GSC_CONNECTOR},
        )
        query = urlencode(
            {
                "client_id": settings.google_client_id,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "response_type": "code",
                "scope": GSC_READONLY_SCOPE,
                "access_type": "offline",
                "include_granted_scopes": "false",
                "prompt": "consent",
                "state": state,
            }
        )
        return connector, f"https://accounts.google.com/o/oauth2/v2/auth?{query}", expires_at

    async def create_sync(
        self, connector_id: UUID, command: ConnectorSyncCreate, idempotency_key: str
    ) -> ConnectorSync:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.DEVELOPER)
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.id == connector_id,
            )
        )
        if connector is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connector_not_found")
        if connector.status != "active" or not connector.secret_ref:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="connector_not_active")
        if command.range_start > command.range_end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_range"
            )
        if (command.range_end - command.range_start).days + 1 > MAX_BACKFILL_DAYS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="range_too_large"
            )
        existing = await self.session.scalar(
            select(ConnectorSync).where(
                ConnectorSync.tenant_id == self.context.tenant_id,
                ConnectorSync.connector_id == connector.id,
                ConnectorSync.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        sync = ConnectorSync(
            tenant_id=self.context.tenant_id,
            connector_id=connector.id,
            kind=command.kind,
            idempotency_key=idempotency_key,
            range_start=command.range_start,
            range_end=command.range_end,
            status="queued",
            counts_json={"days_completed": 0, "rows_seen": 0, "rows_upserted": 0},
            requested_by=self.context.actor_id,
        )
        self.session.add(sync)
        await self.session.flush()
        self.site_service._stage_event(
            "connector.sync_requested",
            "connector_sync",
            sync.id,
            {"connector_id": str(connector.id), "site_id": str(connector.site_id)},
        )
        return sync

    async def connect_dns_provider(
        self,
        site_id: UUID,
        provider_key: str,
        zone_id: str,
        api_token: str,
        provider: DnsProvider,
        secret_store: ConnectorSecretStore,
    ) -> Connector:
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._unverified_or_verified_site(site_id)
        if provider.provider_key != provider_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="dns_provider_not_supported",
            )
        try:
            zone = await provider.get_zone(zone_id, api_token)
        except DnsProviderError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        if zone.id != zone_id or zone.name != site.normalized_host:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="dns_provider_zone_site_mismatch",
            )
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == site.id,
                Connector.type == DNS_PROVIDER_CONNECTOR,
            )
        )
        if connector is None:
            connector = Connector(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                type=DNS_PROVIDER_CONNECTOR,
                provider_key=provider_key,
                status="pending_authorization",
            )
            self.session.add(connector)
            await self.session.flush()
        secret_ref = await secret_store.store(
            self.context.tenant_id, connector.id, f"dns_provider:{provider_key}", {"api_token": api_token}
        )
        now = datetime.now(UTC)
        connector.status = "active"
        connector.provider_key = provider_key
        connector.external_account_ref = zone.id
        connector.secret_ref = secret_ref
        connector.granted_scopes = ["zone:read", "dns:write"]
        connector.consented_by = self.context.actor_id
        connector.consented_at = now
        connector.version = (connector.version or 0) + 1
        self.site_service._stage_event(
            "connector.authorized",
            "connector",
            connector.id,
            {
                "site_id": str(site.id),
                "connector_type": DNS_PROVIDER_CONNECTOR,
                "provider_key": provider_key,
                "zone_id": zone.id,
                "scopes": connector.granted_scopes,
            },
        )
        return connector

    async def publish_dns_provider_challenge(
        self,
        site_id: UUID,
        provider_key: str,
        token: str,
        provider: DnsProvider,
        secret_store: ConnectorSecretReader,
    ) -> tuple[str, str]:
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._unverified_or_verified_site(site_id)
        if provider.provider_key != provider_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="dns_provider_not_supported",
            )
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        challenge = await self.session.scalar(
            select(SiteVerificationChallenge).where(
                SiteVerificationChallenge.tenant_id == self.context.tenant_id,
                SiteVerificationChallenge.site_id == site.id,
                SiteVerificationChallenge.token_hash == token_hash,
                SiteVerificationChallenge.status == "pending",
                SiteVerificationChallenge.expires_at > datetime.now(UTC),
            )
        )
        if challenge is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="verification_challenge_invalid"
            )
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == site.id,
                Connector.type == DNS_PROVIDER_CONNECTOR,
                Connector.provider_key == provider_key,
                Connector.status == "active",
            )
        )
        if connector is None or not connector.secret_ref or not connector.external_account_ref:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="dns_provider_connector_not_active"
            )
        payload = await secret_store.load(
            self.context.tenant_id, connector.id, connector.secret_ref
        )
        api_token = payload.get("api_token")
        if not isinstance(api_token, str) or not api_token:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="dns_provider_connector_secret_invalid"
            )
        record_name = f"_seo-autopilot.{site.normalized_host}"
        try:
            record = await provider.find_txt_record(
                connector.external_account_ref, record_name, token, api_token
            )
            if record is None:
                record = await provider.create_txt_record(
                    connector.external_account_ref, record_name, token, api_token
                )
        except DnsProviderError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        self.site_service._stage_event(
            "site.verification_record_created",
            "verification_challenge",
            challenge.id,
            {
                "site_id": str(site.id),
                "connector_type": DNS_PROVIDER_CONNECTOR,
                "provider_key": provider_key,
                "record_id": record.id,
                "record_name": record.name,
            },
        )
        return record.id, record.name

    async def _unverified_or_verified_site(self, site_id: UUID) -> Site:
        site = await self.site_service.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        return site

    async def _verified_site(self, site_id: UUID) -> Site:
        site = await self.site_service.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        if site.status != "active" or site.verified_at is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="site_not_verified")
        return site


class ConnectorOAuthCallbackService:
    def __init__(
        self,
        session: AsyncSession,
        provider: GoogleOAuthProvider,
        secret_store: ConnectorSecretStore,
    ) -> None:
        self.session = session
        self.provider = provider
        self.secret_store = secret_store

    async def complete_gsc(self, state: str, code: str, trace_id: str) -> Connector:
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        # Two steps, deliberately. The first read is the only statement in this
        # request that is not tenant scoped -- it cannot be, because the tenant
        # is what this row establishes -- so it is a plain SELECT permitted by a
        # single SELECT-only policy. The row's tenant then becomes the scope for
        # everything after it, including the locking re-read that actually
        # consumes the state.
        unscoped = await self.session.scalar(
            select(ConnectorOauthState).where(ConnectorOauthState.state_hash == state_hash)
        )
        if unscoped is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="oauth_state_invalid")
        await self.session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(unscoped.tenant_id)},
        )
        self.session.expunge(unscoped)
        oauth_state = await self.session.scalar(
            select(ConnectorOauthState)
            .where(
                ConnectorOauthState.state_hash == state_hash,
                ConnectorOauthState.tenant_id == unscoped.tenant_id,
            )
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            oauth_state is None
            or oauth_state.consumed_at is not None
            or oauth_state.expires_at <= now
            or oauth_state.requested_scopes != [GSC_READONLY_SCOPE]
            or not oauth_state.requested_property_ref
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="oauth_state_invalid")
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.id == oauth_state.connector_id,
                Connector.tenant_id == oauth_state.tenant_id,
            )
        )
        site = await self.session.scalar(
            select(Site).where(
                Site.id == oauth_state.site_id,
                Site.tenant_id == oauth_state.tenant_id,
            )
        )
        if connector is None or site is None or site.verified_at is None or site.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="oauth_binding_invalid"
            )
        if not property_matches_site(oauth_state.requested_property_ref, site.normalized_host):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="oauth_binding_invalid"
            )
        try:
            grant = await self.provider.exchange_code(code)
            if grant.scopes != frozenset({GSC_READONLY_SCOPE}):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="oauth_scope_mismatch"
                )
            properties = await self.provider.list_properties(grant.access_token)
        except GoogleOAuthError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        matching = next(
            (
                item
                for item in properties
                if item.property_ref == oauth_state.requested_property_ref
                and item.permission_level in GSC_PERMITTED_LEVELS
            ),
            None,
        )
        if matching is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="search_console_property_not_authorized",
            )
        secret_ref = await self.secret_store.store(
            oauth_state.tenant_id,
            connector.id,
            GSC_CONNECTOR,
            {
                "access_token": grant.access_token,
                "refresh_token": grant.refresh_token,
                "expires_at": grant.expires_at.isoformat(),
                "scopes": sorted(grant.scopes),
            },
        )
        oauth_state.consumed_at = now
        connector.status = "active"
        connector.external_account_ref = matching.property_ref
        connector.secret_ref = secret_ref
        connector.granted_scopes = sorted(grant.scopes)
        connector.consented_by = oauth_state.created_by
        connector.consented_at = now
        connector.token_expires_at = grant.expires_at
        connector.version += 1
        payload = {
            "connector_id": str(connector.id),
            "site_id": str(site.id),
            "connector_type": GSC_CONNECTOR,
            "property_ref": matching.property_ref,
            "scopes": sorted(grant.scopes),
        }
        self.session.add_all(
            [
                AuditEvent(
                    tenant_id=oauth_state.tenant_id,
                    actor_type="user",
                    actor_id=str(oauth_state.created_by),
                    action="connector.authorized",
                    resource_type="connector",
                    resource_id=str(connector.id),
                    trace_id=trace_id,
                    metadata_json=payload,
                    event_hash=stable_hash(payload),
                ),
                OutboxEvent(
                    tenant_id=oauth_state.tenant_id,
                    event_type="connector.authorized",
                    event_version=1,
                    aggregate_type="connector",
                    aggregate_id=connector.id,
                    payload=payload,
                ),
            ]
        )
        return connector
