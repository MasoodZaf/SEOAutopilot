import hashlib
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ConnectorSyncCreate
from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    Connector,
    ConnectorOauthState,
    ConnectorSecret,
    ConnectorSync,
    OutboxEvent,
    Site,
    SiteVerificationChallenge,
)
from app.services.connector_secrets import ConnectorSecretReader, ConnectorSecretStore
from app.services.dns_provider import DnsProvider, DnsProviderError
from app.services.google_analytics import (
    ANALYTICS_READONLY_SCOPE,
    AnalyticsAdminProvider,
    GoogleAnalyticsError,
)
from app.services.google_analytics import (
    property_matches_site as analytics_property_matches_site,
)
from app.services.google_oauth import GoogleOAuthError, GoogleOAuthProvider
from app.services.sites import SiteService, stable_hash
from app.services.tenant_credentials import GoogleOAuthClient, google_oauth_client

GSC_CONNECTOR = "google_search_console"
DNS_PROVIDER_CONNECTOR = "dns_provider"
GSC_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
MAX_BACKFILL_DAYS = 490
GSC_PERMITTED_LEVELS = frozenset({"siteOwner", "siteFullUser", "siteRestrictedUser"})
ANALYTICS_CONNECTOR = "google_analytics"

# The scope sets a callback will act on. A state carrying anything else -- a
# scope that was widened after the state was written, or one this build does
# not implement -- is not a state this code knows how to bind, so it is refused
# rather than guessed at.
# One consent for both Google connectors, on every verified site at once.
WORKSPACE_GOOGLE_SCOPES = sorted([GSC_READONLY_SCOPE, ANALYTICS_READONLY_SCOPE])
_SUPPORTED_SCOPE_SETS = [
    [GSC_READONLY_SCOPE],
    [ANALYTICS_READONLY_SCOPE],
    WORKSPACE_GOOGLE_SCOPES,
]
_ANALYTICS_PROPERTY_PATTERN = re.compile(r"properties/[0-9]{1,20}")


@dataclass(frozen=True, slots=True)
class WorkspaceGrantOutcome:
    """What one workspace-wide Google consent linked, and what it could not."""

    linked: tuple[tuple[str, str, str], ...]
    unmatched: tuple[tuple[str, str], ...]
    granted_scopes: tuple[str, ...]


def search_console_preference(property_ref: str, normalized_host: str) -> int:
    """Lower is better, among properties that already cover the host.

    A domain property on the host itself holds everything the site has --
    every protocol and subdomain -- so it is the one to read when it exists.
    A parent domain property comes next, then the HTTPS URL-prefix property
    that spells the host, then anything else that still matched.
    """
    host = normalized_host.rstrip(".").lower()
    if property_ref == f"sc-domain:{host}":
        return 0
    if property_ref.startswith("sc-domain:"):
        return 1
    if property_ref.rstrip("/") == f"https://{host}":
        return 2
    return 3


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

    async def _google_client(self, settings: Settings) -> GoogleOAuthClient:
        """The OAuth client this tenant's consent should run through.

        Their own if they have configured one, the deployment's only if an
        operator deliberately left one set. A tenant with neither is refused
        here rather than sent to Google to be told, unhelpfully, that the
        client id is invalid.
        """
        if not settings.google_connectors_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="google_connector_not_configured",
            )
        client = await google_oauth_client(self.session, settings, self.context.tenant_id)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="google_oauth_client_not_configured",
            )
        return client

    async def list_for_site(self, site_id: UUID) -> list[Connector] | None:
        if await self.site_service.get_site(site_id) is None:
            return None
        result = await self.session.scalars(
            select(Connector)
            .where(Connector.tenant_id == self.context.tenant_id, Connector.site_id == site_id)
            .order_by(Connector.created_at, Connector.id)
        )
        return list(result)

    async def list_for_tenant(self) -> list[tuple[Connector, Site]]:
        """Every connector in the workspace, with the site it belongs to.

        One query rather than one per site: this backs the connection manager
        and the banner on every signed-in page, and the per-site route would
        make that a request fan-out on each render.
        """
        rows = await self.session.execute(
            select(Connector, Site)
            .join(Site, (Site.id == Connector.site_id) & (Site.tenant_id == Connector.tenant_id))
            .where(Connector.tenant_id == self.context.tenant_id)
            .order_by(Site.name, Site.id, Connector.type)
        )
        return [(connector, site) for connector, site in rows.tuples()]

    async def disconnect(self, connector_id: UUID) -> Connector:
        """Stop using a connector and destroy what we hold for it.

        Deliberately local. Google's revoke endpoint does not revoke one token:
        it removes this application's access from the Google account, which
        takes every other connector consented through that account down with
        it -- on this site and every other one. A person clicking "disconnect"
        on one site's Analytics must not silently break another site's Search
        Console. So the sealed grant is revoked here, where it can no longer be
        read by anything, and the page points at the account's own permissions
        page for anyone who wants the provider to forget us too.

        Idempotent: disconnecting a disconnected connector returns it unchanged.
        """
        self.context.require(Role.OWNER, Role.ADMIN)
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.id == connector_id,
            )
        )
        if connector is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connector_not_found")
        if connector.status == "revoked":
            return connector
        await self.session.execute(
            update(ConnectorSecret)
            .where(
                ConnectorSecret.tenant_id == self.context.tenant_id,
                ConnectorSecret.connector_id == connector.id,
                ConnectorSecret.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        previous = connector.status
        connector.status = "revoked"
        connector.secret_ref = None
        connector.token_expires_at = None
        connector.last_error_code = None
        connector.version += 1
        self.site_service._stage_event(
            "connector.revoked",
            "connector",
            connector.id,
            {
                "site_id": str(connector.site_id),
                "connector_type": connector.type,
                "previous_status": previous,
            },
        )
        return connector

    async def begin_google_authorization(self, settings: Settings) -> tuple[str, datetime]:
        """One Google consent that connects Search Console and GA4 on every site.

        Nothing is bound here and no connector changes state: which property
        belongs to which site is only knowable once the grant exists and
        Google can be asked what the account sees. A consent that is started
        and abandoned therefore breaks nothing that was working.

        The state row still needs a site and a connector to hang from, so it
        uses the first verified site's Search Console connector, creating a
        pending one if the site has none yet.
        """
        self.context.require(Role.OWNER, Role.ADMIN)
        client = await self._google_client(settings)
        anchor = await self.session.scalar(
            select(Site)
            .where(
                Site.tenant_id == self.context.tenant_id,
                Site.status == "active",
                Site.verified_at.is_not(None),
            )
            .order_by(Site.created_at, Site.id)
            .limit(1)
        )
        if anchor is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="site_not_verified")
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == anchor.id,
                Connector.type == GSC_CONNECTOR,
            )
        )
        if connector is None:
            connector = Connector(
                tenant_id=self.context.tenant_id,
                site_id=anchor.id,
                type=GSC_CONNECTOR,
                status="pending_authorization",
            )
            self.session.add(connector)
            await self.session.flush()

        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        self.session.add(
            ConnectorOauthState(
                tenant_id=self.context.tenant_id,
                site_id=anchor.id,
                connector_id=connector.id,
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                requested_scopes=WORKSPACE_GOOGLE_SCOPES,
                requested_property_ref=None,
                expires_at=expires_at,
                created_by=self.context.actor_id,
            )
        )
        self.site_service._stage_event(
            "connector.authorization_started",
            "connector",
            connector.id,
            {"site_id": str(anchor.id), "connector_type": "google_workspace"},
        )
        query = urlencode(
            {
                "client_id": client.client_id,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "response_type": "code",
                "scope": " ".join(WORKSPACE_GOOGLE_SCOPES),
                "access_type": "offline",
                "include_granted_scopes": "false",
                "prompt": "consent",
                "state": state,
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}", expires_at

    async def begin_analytics_authorization(
        self, site_id: UUID, property_ref: str, settings: Settings
    ) -> tuple[Connector, str, datetime]:
        """Start a GA4 consent, without claiming the property is ours yet.

        Deliberately different from the Search Console flow in one respect: a
        Search Console property ref names its own site, so it can be rejected
        here, before anyone is sent to Google. A GA4 ref names nothing, so the
        binding cannot happen until the grant exists and the property's data
        streams can be read. What is checked here is only its shape.
        """
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._verified_site(site_id)
        client = await self._google_client(settings)
        if not _ANALYTICS_PROPERTY_PATTERN.fullmatch(property_ref):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="analytics_property_ref_invalid",
            )
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == site.id,
                Connector.type == ANALYTICS_CONNECTOR,
            )
        )
        if connector is None:
            connector = Connector(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                type=ANALYTICS_CONNECTOR,
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
                requested_scopes=[ANALYTICS_READONLY_SCOPE],
                requested_property_ref=property_ref,
                expires_at=expires_at,
                created_by=self.context.actor_id,
            )
        )
        self.site_service._stage_event(
            "connector.authorization_started",
            "connector",
            connector.id,
            {"site_id": str(site.id), "connector_type": ANALYTICS_CONNECTOR},
        )
        query = urlencode(
            {
                "client_id": client.client_id,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "response_type": "code",
                "scope": ANALYTICS_READONLY_SCOPE,
                "access_type": "offline",
                "include_granted_scopes": "false",
                "prompt": "consent",
                "state": state,
            }
        )
        return connector, f"https://accounts.google.com/o/oauth2/v2/auth?{query}", expires_at

    async def begin_gsc_authorization(
        self, site_id: UUID, property_ref: str, settings: Settings
    ) -> tuple[Connector, str, datetime]:
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._verified_site(site_id)
        client = await self._google_client(settings)
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
                "client_id": client.client_id,
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
            counts_json={"days_completed": 0, "rows_seen": 0, "rows_upserted": 0, "rows_new": 0},
            requested_by=self.context.actor_id,
        )
        # The lookup above only serialises retries that arrive one after
        # another. Let the unique constraint decide between two that overlap,
        # and hand the loser the sync its twin created rather than a 500.
        try:
            async with self.session.begin_nested():
                self.session.add(sync)
                await self.session.flush()
        except IntegrityError:
            concurrent = await self.session.scalar(
                select(ConnectorSync).where(
                    ConnectorSync.tenant_id == self.context.tenant_id,
                    ConnectorSync.connector_id == connector.id,
                    ConnectorSync.idempotency_key == idempotency_key,
                )
            )
            if concurrent is None:
                raise
            return concurrent
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
        provider: GoogleOAuthProvider | None,
        secret_store: ConnectorSecretStore,
        analytics: AnalyticsAdminProvider | None = None,
        *,
        provider_factory: Callable[[UUID], Awaitable[GoogleOAuthProvider]] | None = None,
    ) -> None:
        self.session = session
        self._provider = provider
        self.secret_store = secret_store
        self.analytics = analytics
        # The code must be exchanged with the same OAuth client that issued it,
        # and which client that is depends on the tenant -- which is precisely
        # what the state row establishes and nothing before it knows. So the
        # caller may hand over a factory instead of a provider, and it is
        # called once the tenant is resolved. Tests and the single-client
        # deployments that predate per-tenant credentials still pass a
        # provider directly.
        self.provider_factory = provider_factory
        # Set by a workspace-wide consent, so the route can say what it linked.
        self.outcome: WorkspaceGrantOutcome | None = None

    @property
    def provider(self) -> GoogleOAuthProvider:
        if self._provider is None:  # pragma: no cover - guarded by construction
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="google_oauth_client_not_configured",
            )
        return self._provider

    async def _search_console_property(self, grant, oauth_state) -> str:
        """The requested property, only if this grant actually covers it.

        Search Console returns the properties the authorizing account can see
        and at what level. A property absent from that list, or present at a
        level too low to read metrics, is not one this grant can use.
        """
        properties = await self.provider.list_properties(grant.access_token)
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
        return matching.property_ref

    async def _analytics_property(self, grant, oauth_state, site: Site) -> str:
        """The requested GA4 property, only if it measures this very site.

        Two separate facts, and both are checked here rather than at request
        time: the grant covers the property, and one of the property's own data
        streams collects from the host the tenant already proved they control.
        A property ref alone claims neither.
        """
        if self.analytics is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="google_analytics_connector_not_configured",
            )
        properties = await self.analytics.list_properties(grant.access_token)
        matching = next(
            (
                item
                for item in properties
                if item.property_ref == oauth_state.requested_property_ref
            ),
            None,
        )
        if matching is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="analytics_property_not_authorized",
            )
        if not analytics_property_matches_site(matching, site.normalized_host):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="analytics_property_site_mismatch",
            )
        return matching.property_ref

    async def _complete_workspace(
        self, oauth_state: ConnectorOauthState, code: str, trace_id: str, now: datetime
    ) -> WorkspaceGrantOutcome:
        """Bind one grant to every verified site whose properties it can see.

        Google lets a person untick a scope on the consent screen, so the grant
        may cover only one of the two services; each is bound only if granted.
        For every site the property is found rather than asked for: Search
        Console by what the account can read and what covers the host, GA4 by
        which property has a data stream on the host.

        A connector already working is never moved to a different property. It
        takes the new grant only if this account can see the property it
        already reads -- so consenting with a second Google account refreshes
        what that account can see and leaves every other site as it was.
        """
        try:
            grant = await self.provider.exchange_code(code)
        except GoogleOAuthError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        granted = set(grant.scopes)
        if not granted or not granted <= set(WORKSPACE_GOOGLE_SCOPES):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="oauth_scope_mismatch"
            )
        try:
            search_properties = (
                await self.provider.list_properties(grant.access_token)
                if GSC_READONLY_SCOPE in granted
                else []
            )
            if ANALYTICS_READONLY_SCOPE in granted:
                if self.analytics is None:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="google_analytics_connector_not_configured",
                    )
                analytics_properties = await self.analytics.list_properties(grant.access_token)
            else:
                analytics_properties = []
        except (GoogleOAuthError, GoogleAnalyticsError) as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error

        sites = list(
            await self.session.scalars(
                select(Site)
                .where(
                    Site.tenant_id == oauth_state.tenant_id,
                    Site.status == "active",
                    Site.verified_at.is_not(None),
                )
                .order_by(Site.name, Site.id)
            )
        )
        linked: list[tuple[str, str, str]] = []
        unmatched: list[tuple[str, str]] = []
        for site in sites:
            if GSC_READONLY_SCOPE in granted:
                candidates = sorted(
                    (
                        item.property_ref
                        for item in search_properties
                        if item.permission_level in GSC_PERMITTED_LEVELS
                        and property_matches_site(item.property_ref, site.normalized_host)
                    ),
                    key=lambda ref: (search_console_preference(ref, site.normalized_host), ref),
                )
                bound = await self._bind_google(
                    oauth_state, site, GSC_CONNECTOR, candidates, grant, now, trace_id
                )
                if bound:
                    linked.append((site.normalized_host, GSC_CONNECTOR, bound))
                else:
                    unmatched.append((site.normalized_host, GSC_CONNECTOR))
            if ANALYTICS_READONLY_SCOPE in granted:
                host = site.normalized_host.rstrip(".").lower()
                candidates = [
                    item.property_ref
                    for item in sorted(
                        (
                            item
                            for item in analytics_properties
                            if analytics_property_matches_site(item, site.normalized_host)
                        ),
                        # A stream on the host itself before one on a parent.
                        key=lambda item: (host not in item.stream_hosts, item.property_ref),
                    )
                ]
                bound = await self._bind_google(
                    oauth_state, site, ANALYTICS_CONNECTOR, candidates, grant, now, trace_id
                )
                if bound:
                    linked.append((site.normalized_host, ANALYTICS_CONNECTOR, bound))
                else:
                    unmatched.append((site.normalized_host, ANALYTICS_CONNECTOR))
        if not linked:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="google_no_matching_properties"
            )
        oauth_state.consumed_at = now
        return WorkspaceGrantOutcome(
            linked=tuple(linked),
            unmatched=tuple(unmatched),
            granted_scopes=tuple(sorted(granted)),
        )

    async def _bind_google(
        self,
        oauth_state: ConnectorOauthState,
        site: Site,
        connector_type: str,
        candidates: list[str],
        grant,
        now: datetime,
        trace_id: str,
    ) -> str | None:
        """Give one site's connector this grant, if a property fits. Returns it."""
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == oauth_state.tenant_id,
                Connector.site_id == site.id,
                Connector.type == connector_type,
            )
        )
        current = connector.external_account_ref if connector is not None else None
        if connector is not None and connector.status == "active":
            if current not in candidates:
                return None
            property_ref = current
        elif not candidates:
            return None
        else:
            # Reconnecting: keep the property it read before, if still visible.
            property_ref = current if current in candidates else candidates[0]
        if connector is None:
            connector = Connector(
                tenant_id=oauth_state.tenant_id,
                site_id=site.id,
                type=connector_type,
                status="pending_authorization",
            )
            self.session.add(connector)
            await self.session.flush()
        assert property_ref is not None
        secret_ref = await self.secret_store.store(
            oauth_state.tenant_id,
            connector.id,
            connector_type,
            {
                "access_token": grant.access_token,
                "refresh_token": grant.refresh_token,
                "expires_at": grant.expires_at.isoformat(),
                "scopes": sorted(grant.scopes),
            },
        )
        connector.status = "active"
        connector.external_account_ref = property_ref
        connector.secret_ref = secret_ref
        connector.granted_scopes = sorted(grant.scopes)
        connector.consented_by = oauth_state.created_by
        connector.consented_at = now
        connector.token_expires_at = grant.expires_at
        connector.last_checked_at = now
        connector.last_error_code = None
        connector.version = (connector.version or 0) + 1
        payload = {
            "connector_id": str(connector.id),
            "site_id": str(site.id),
            "connector_type": connector_type,
            "property_ref": property_ref,
            "scopes": sorted(grant.scopes),
            "consent": "workspace",
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
        return property_ref

    async def complete_authorization(
        self, state: str, code: str, trace_id: str
    ) -> Connector | None:
        """Redeem one Google consent, whichever connector asked for it.

        The state row says which: it carries the scopes that were requested, and
        only those two sets are ones this build knows how to bind to a site.
        Everything before the exchange is shared deliberately -- the tenant
        adoption and the single-use consume are the parts that must not be
        reimplemented once per connector.
        """
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
        # The tenant is known now, so the client that started this consent can
        # be resolved. Done here rather than later so that a tenant whose
        # credential was revoked mid-flow is refused before the code is spent.
        if self.provider_factory is not None:
            self._provider = await self.provider_factory(unscoped.tenant_id)
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
            or sorted(oauth_state.requested_scopes) not in _SUPPORTED_SCOPE_SETS
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="oauth_state_invalid")
        if sorted(oauth_state.requested_scopes) == WORKSPACE_GOOGLE_SCOPES:
            self.outcome = await self._complete_workspace(oauth_state, code, trace_id, now)
            return None
        if not oauth_state.requested_property_ref:
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
        wanted = sorted(oauth_state.requested_scopes)
        connector_type = GSC_CONNECTOR if wanted == [GSC_READONLY_SCOPE] else ANALYTICS_CONNECTOR
        if connector_type == GSC_CONNECTOR and not property_matches_site(
            oauth_state.requested_property_ref, site.normalized_host
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="oauth_binding_invalid"
            )
        try:
            grant = await self.provider.exchange_code(code)
            if sorted(grant.scopes) != wanted:
                # Google grants what the user consented to, not what we asked
                # for. A grant carrying a different scope set is not the one
                # this state authorized.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="oauth_scope_mismatch"
                )
            if connector_type == GSC_CONNECTOR:
                property_ref = await self._search_console_property(grant, oauth_state)
            else:
                property_ref = await self._analytics_property(grant, oauth_state, site)
        except (GoogleOAuthError, GoogleAnalyticsError) as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        secret_ref = await self.secret_store.store(
            oauth_state.tenant_id,
            connector.id,
            connector_type,
            {
                "access_token": grant.access_token,
                "refresh_token": grant.refresh_token,
                "expires_at": grant.expires_at.isoformat(),
                "scopes": sorted(grant.scopes),
            },
        )
        oauth_state.consumed_at = now
        connector.status = "active"
        connector.external_account_ref = property_ref
        connector.secret_ref = secret_ref
        connector.granted_scopes = sorted(grant.scopes)
        connector.consented_by = oauth_state.created_by
        connector.consented_at = now
        connector.token_expires_at = grant.expires_at
        connector.last_checked_at = now
        connector.last_error_code = None
        connector.version += 1
        payload = {
            "connector_id": str(connector.id),
            "site_id": str(site.id),
            "connector_type": connector_type,
            "property_ref": property_ref,
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
