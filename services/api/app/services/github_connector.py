"""The repository a site deploys to, and the credential that reaches it.

Until now both were install-wide settings: one `GITHUB_REPOSITORY`, one
`GITHUB_TOKEN`, read straight out of the environment by four call sites. That is
serviceable for a single-tenant pilot and indefensible the moment there are two
tenants, because tenant A's approved change would be pushed with tenant B's
credential, to a repository neither of them chose per site.

This module makes the repository and the credential properties of a *site*,
stored on a connector row beside the Search Console and DNS connectors that
already work this way. Two shapes are supported, and they are not equals:

  github_app  The tenant installs the app on the repositories they pick. Nothing
              secret is stored -- the installation id is a public reference --
              and a token is minted per deployment, lives an hour, and is scoped
              by GitHub to the repositories the tenant granted. This is the one
              to use.

  github_pat  A personal access token, encrypted through the same envelope the
              DNS connector uses. It exists because a pilot already running on a
              PAT should be able to move onto a per-site connector without
              waiting for an app registration, and because GitHub Enterprise
              installs cannot always use an app. It is long-lived and belongs to
              a person, so it is recorded as such.

`resolve` is the one function the deployment paths call. It answers with a
repository, a token, and where that token came from -- and refuses when a site
has no connector, rather than silently reaching for an install-wide default.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    Connector,
    ConnectorOauthState,
    ConnectorSecret,
    Site,
)
from app.domain.github_adapter import GitHubTarget
from app.services.connector_secrets import ConnectorSecretReader, ConnectorSecretStore
from app.services.github_app import GitHubAppClient, GitHubAppError, GitHubUserClient
from app.services.sites import SiteService, stable_hash
from app.services.tenant_credentials import GitHubAppCredential, github_app_credential

GITHUB_CONNECTOR = "github_repository"
PROVIDER_APP = "github_app"
PROVIDER_PAT = "github_pat"

# What a deployment actually needs: write a file, open a pull request. It does
# not need administration, workflows, or the ability to merge, and asking for
# them would make the connector more powerful than the product's own claim that
# it only ever proposes.
REQUIRED_PERMISSIONS = {"contents": "write", "pull_requests": "write"}
GITHUB_SCOPES = ["contents:write", "pull_requests:write"]

# The connect flow's two steps, told apart by what the state row asks for. A
# state issued for one cannot be redeemed as the other, nor as a Google one.
GITHUB_SIGN_IN = ["github:sign_in"]
GITHUB_CHOICE = ["github:choose_repository"]

# Long enough to install the app on GitHub's page in between; the state is
# single-use and bound to the person who started it either way.
SIGN_IN_STATE_TTL = timedelta(minutes=30)
# How long the repositories GitHub offered stay choosable. Push access is read
# at sign-in, so this bounds how stale that answer can be when it is acted on.
CHOICE_TTL = timedelta(minutes=15)
# Installing the app sends the person back through sign-in. More round trips
# than this is a loop, not a person.
MAX_SIGN_IN_BOUNCES = 3
MAX_CHOICES = 1000


class GitHubConnectorError(Exception):
    """A repository could not be reached with the credential offered."""


@dataclass(frozen=True, slots=True)
class RepositoryFacts:
    full_name: str
    default_branch: str
    can_push: bool
    archived: bool


class RepositoryProbe(Protocol):
    async def inspect(self, slug: str, token: str) -> RepositoryFacts: ...


class GitHubRepositoryHttpProbe:
    """Asks GitHub what a credential can actually do with a repository.

    Storing a token without trying it means the first thing a tenant learns
    about a mistyped repository or an under-scoped token is a failed deployment
    on an approved change. The connector is only marked active once this has
    come back.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def inspect(self, slug: str, token: str) -> RepositoryFacts:
        try:
            response = await self._client.get(
                f"https://api.github.com/repos/{slug}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.TimeoutException as error:
            raise GitHubConnectorError("github_timeout") from error
        except httpx.HTTPError as error:
            raise GitHubConnectorError("github_unavailable") from error
        if response.status_code in (403, 404):
            # GitHub returns 404 for a repository the credential cannot see, so
            # "does not exist" and "not granted" are the same answer, and saying
            # which would leak the existence of private repositories.
            raise GitHubConnectorError("github_repository_not_accessible")
        if response.status_code != 200:
            raise GitHubConnectorError(f"github_repository_lookup_failed:{response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise GitHubConnectorError("github_repository_malformed")
        permissions = body.get("permissions")
        return RepositoryFacts(
            full_name=str(body.get("full_name") or slug),
            default_branch=str(body.get("default_branch") or "main"),
            can_push=bool(isinstance(permissions, dict) and permissions.get("push")),
            archived=bool(body.get("archived")),
        )


@dataclass(frozen=True, slots=True)
class GitHubCredential:
    """Everything a deployment needs, and where the authority for it came from."""

    target: GitHubTarget
    token: str
    path_template: str
    connector_id: UUID | None
    source: str


def parse_repository(raw: str) -> tuple[str, str] | None:
    """Split `owner/repository`, or nothing if it is not that shape."""
    owner, separator, repository = (raw or "").strip().partition("/")
    if not separator or not owner or not repository or "/" in repository:
        return None
    if any(character in f"{owner}{repository}" for character in " \t\n?#@:"):
        return None
    return owner, repository


def normalize_path_template(raw: str) -> str:
    """A template that maps a crawled URL path to a file in the repository.

    It is interpolated into a request path against GitHub's contents API, so a
    template that can escape the repository -- or that ignores the path
    entirely and writes the same file for every page -- is refused here rather
    than discovered at deploy time.
    """
    template = (raw or "").strip()
    if "{path}" not in template:
        raise ValueError("path_template_missing_path")
    if template.startswith("/") or ".." in template or "\\" in template:
        raise ValueError("path_template_unsafe")
    if len(template) > 200:
        raise ValueError("path_template_too_long")
    return template


def github_settings_target(settings: Settings) -> GitHubTarget | None:
    """The install-wide fallback target, or nothing if it is unusable."""
    parsed = parse_repository(settings.github_repository or "")
    if parsed is None:
        return None
    owner, repository = parsed
    return GitHubTarget(
        owner=owner, repository=repository, base_branch=settings.github_base_branch
    )


def _connector_target(connector: Connector) -> GitHubTarget:
    parsed = parse_repository(connector.external_account_ref or "")
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="github_connector_repository_invalid"
        )
    owner, repository = parsed
    config = connector.config_json or {}
    return GitHubTarget(
        owner=owner,
        repository=repository,
        base_branch=str(config.get("base_branch") or "main"),
    )


class GitHubConnectorService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.site_service = SiteService(session, context)

    async def _site(self, site_id: UUID) -> Site:
        """A site nobody has proved they own must not gain a write connector.

        Search Console verification is the product's evidence that this tenant
        controls the domain. Binding a repository to an unverified site would
        let someone point deployments at a website they merely named.
        """
        site = await self.site_service.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        if site.status != "active" or site.verified_at is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="site_not_verified")
        return site

    async def _connector_for(self, site_id: UUID) -> Connector | None:
        return await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == site_id,
                Connector.type == GITHUB_CONNECTOR,
            )
        )

    async def _upsert(self, site: Site, provider_key: str) -> Connector:
        connector = await self._connector_for(site.id)
        if connector is None:
            connector = Connector(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                type=GITHUB_CONNECTOR,
                provider_key=provider_key,
                status="pending_authorization",
            )
            self.session.add(connector)
            await self.session.flush()
        return connector

    async def connect_personal_access_token(
        self,
        site_id: UUID,
        repository: str,
        base_branch: str,
        path_template: str,
        token: str,
        probe: RepositoryProbe,
        secret_store: ConnectorSecretStore,
    ) -> Connector:
        """Store a tenant-supplied token, after proving it can do the work."""
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._site(site_id)
        parsed = parse_repository(repository)
        if parsed is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="github_repository_invalid",
            )
        slug = f"{parsed[0]}/{parsed[1]}"
        try:
            facts = await probe.inspect(slug, token)
        except GitHubConnectorError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        if not facts.can_push:
            # Read access is enough to draft a proposal and useless for
            # deploying one. Accepting it would mean the connector reports
            # healthy and fails on the first approved change.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="github_token_cannot_write",
            )
        if facts.archived:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="github_repository_archived",
            )

        connector = await self._upsert(site, PROVIDER_PAT)
        secret_ref = await secret_store.store(
            self.context.tenant_id, connector.id, PROVIDER_PAT, {"access_token": token}
        )
        now = datetime.now(UTC)
        connector.provider_key = PROVIDER_PAT
        connector.status = "active"
        connector.external_account_ref = facts.full_name
        connector.secret_ref = secret_ref
        connector.config_json = {
            "base_branch": base_branch or facts.default_branch,
            "path_template": path_template,
        }
        connector.granted_scopes = list(GITHUB_SCOPES)
        connector.consented_by = self.context.actor_id
        connector.consented_at = now
        connector.token_expires_at = None
        connector.version = (connector.version or 0) + 1
        self.site_service._stage_event(
            "connector.authorized",
            "connector",
            connector.id,
            {
                "site_id": str(site.id),
                "connector_type": GITHUB_CONNECTOR,
                "provider_key": PROVIDER_PAT,
                "repository": facts.full_name,
                "base_branch": connector.config_json["base_branch"],
                # A long-lived credential held by a person is a different risk
                # from an installation grant, and the audit trail should not
                # have to infer which one a site is running on.
                "credential_is_long_lived": True,
            },
        )
        return connector

    async def _signing_app(self, settings: Settings) -> GitHubAppCredential:
        app = await github_app_credential(self.session, settings, self.context.tenant_id)
        if app is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="github_app_not_configured",
            )
        if not app.can_sign_in:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="github_app_sign_in_not_configured",
            )
        return app

    async def begin_sign_in(
        self, site_id: UUID, settings: Settings, *, install: bool = False
    ) -> tuple[str, datetime]:
        """Send the person to GitHub, as Claude or ChatGPT would.

        GitHub asks them to authorize the app (a single click after the first
        time) and, when `install` is set or they have no installation yet, to
        choose which repositories the app may reach. What comes back is a
        person GitHub vouches for, not a claim typed into a form.

        Nothing about the site's connector changes here, so a site deploying
        today keeps deploying if the person abandons GitHub's page.
        """
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._site(site_id)
        app = await self._signing_app(settings)
        connector = await self._upsert(site, PROVIDER_APP)

        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + SIGN_IN_STATE_TTL
        self.session.add(
            ConnectorOauthState(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                connector_id=connector.id,
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                requested_scopes=list(GITHUB_SIGN_IN),
                requested_config={"bounces": 0},
                expires_at=expires_at,
                created_by=self.context.actor_id,
            )
        )
        self.site_service._stage_event(
            "connector.authorization_started",
            "connector",
            connector.id,
            {
                "site_id": str(site.id),
                "connector_type": GITHUB_CONNECTOR,
                "provider_key": PROVIDER_APP,
                "step": "install" if install else "sign_in",
            },
        )
        url = install_url(app.app_slug, state) if install else sign_in_url(app, settings, state)
        return url, expires_at

    async def _choice(self, site_id: UUID, *, lock: bool = False) -> ConnectorOauthState | None:
        """What GitHub offered this person for this site, while it is fresh.

        Bound to the actor as well as the tenant: another admin in the same
        workspace has their own GitHub access, and must not choose from a list
        drawn up against somebody else's.
        """
        query = (
            select(ConnectorOauthState)
            .where(
                ConnectorOauthState.tenant_id == self.context.tenant_id,
                ConnectorOauthState.site_id == site_id,
                ConnectorOauthState.created_by == self.context.actor_id,
                ConnectorOauthState.requested_scopes == list(GITHUB_CHOICE),
                ConnectorOauthState.consumed_at.is_(None),
                ConnectorOauthState.expires_at > datetime.now(UTC),
            )
            .order_by(ConnectorOauthState.created_at.desc())
            .limit(1)
        )
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def repository_choices(
        self, site_id: UUID
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], datetime | None]:
        self.context.require(Role.OWNER, Role.ADMIN)
        if await self.site_service.get_site(site_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        choice = await self._choice(site_id)
        if choice is None:
            return [], [], None
        config = choice.requested_config or {}
        listed = config.get("repositories")
        configure = config.get("configure")
        return (
            list(listed) if isinstance(listed, list) else [],
            list(configure) if isinstance(configure, list) else [],
            choice.expires_at,
        )

    async def choose_repository(
        self,
        site_id: UUID,
        repository_id: int,
        base_branch: str,
        path_template: str,
        settings: Settings,
        client: httpx.AsyncClient,
    ) -> Connector:
        """Bind the repository the person picked, after asking GitHub again.

        The pick is looked up by GitHub's numeric id in the list GitHub itself
        produced at sign-in, never by a name from the browser. The installation
        is then asked, as the app, whether it still reaches that repository
        with the permissions a deployment needs -- the grant may have been
        narrowed in the minutes since.
        """
        self.context.require(Role.OWNER, Role.ADMIN)
        site = await self._site(site_id)
        choice = await self._choice(site.id, lock=True)
        if choice is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="github_choice_expired"
            )
        listed = (choice.requested_config or {}).get("repositories")
        picked = next(
            (
                item
                for item in (listed if isinstance(listed, list) else [])
                if isinstance(item, dict) and item.get("id") == repository_id
            ),
            None,
        )
        if picked is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="github_repository_not_offered"
            )
        slug = str(picked["full_name"])
        installation_id = int(picked["installation_id"])
        template = normalize_path_template(path_template)

        app = await github_app_credential(self.session, settings, self.context.tenant_id)
        if app is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="github_app_not_configured",
            )
        app_client = GitHubAppClient(client, app.app_id, app.private_key)
        try:
            installation = await app_client.installation(installation_id)
            minted = await app_client.mint_installation_token(installation_id)
            granted = (
                minted.repositories
                if minted.repositories is not None
                else await app_client.installation_repositories(minted.token)
            )
        except GitHubAppError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        permissions = installation.get("permissions")
        if not isinstance(permissions, dict) or any(
            permissions.get(name) != level for name, level in REQUIRED_PERMISSIONS.items()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="github_installation_permissions_insufficient",
            )
        if slug not in granted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="github_repository_not_installed",
            )

        now = datetime.now(UTC)
        choice.consumed_at = now
        connector = await self._upsert(site, PROVIDER_APP)
        if connector.secret_ref is not None:
            # Moving from a stored token to an installation. The old credential
            # stops being referenced here, so it is revoked here too rather than
            # left decryptable in a table nothing points at any more.
            await self.session.execute(
                update(ConnectorSecret)
                .where(
                    ConnectorSecret.tenant_id == self.context.tenant_id,
                    ConnectorSecret.connector_id == connector.id,
                    ConnectorSecret.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
        connector.status = "active"
        connector.provider_key = PROVIDER_APP
        connector.external_account_ref = slug
        connector.secret_ref = None
        connector.config_json = {
            "base_branch": base_branch or str(picked.get("default_branch") or "main"),
            "path_template": template,
            "installation_id": installation_id,
            "repository_id": repository_id,
            "account": str(picked.get("account") or ""),
        }
        connector.granted_scopes = list(GITHUB_SCOPES)
        connector.consented_by = self.context.actor_id
        connector.consented_at = now
        # Deliberately null. The token this installation mints is never stored,
        # so there is no expiry for anything to track.
        connector.token_expires_at = None
        connector.last_error_code = None
        connector.version = (connector.version or 0) + 1
        self.site_service._stage_event(
            "connector.authorized",
            "connector",
            connector.id,
            {
                "site_id": str(site.id),
                "connector_type": GITHUB_CONNECTOR,
                "provider_key": PROVIDER_APP,
                "repository": slug,
                "repository_id": repository_id,
                "installation_id": installation_id,
                "base_branch": connector.config_json["base_branch"],
                "credential_is_long_lived": False,
            },
        )
        return connector

    async def resolve(
        self,
        site_id: UUID,
        settings: Settings,
        client: httpx.AsyncClient,
        secret_store: ConnectorSecretReader | None = None,
    ) -> GitHubCredential:
        """The repository and credential this site deploys with.

        Refuses rather than falling back to an install-wide token, unless that
        fallback has been switched on deliberately -- which is how the running
        pilot keeps working for exactly as long as it takes to move it onto a
        connector.
        """
        if await self.site_service.get_site(site_id) is None:
            # A site this tenant cannot see yields no credential of any kind.
            # Without this the install-wide fallback would answer for a site id
            # belonging to somebody else -- row-level security hides their
            # connector, which looks exactly like having none.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")

        connector = await self._connector_for(site_id)
        if connector is not None and connector.status == "active":
            target = _connector_target(connector)
            path_template = str(
                (connector.config_json or {}).get("path_template")
                or settings.github_path_template
            )
            if connector.provider_key == PROVIDER_APP:
                token = await self._installation_token(connector, target, settings, client)
            elif connector.provider_key == PROVIDER_PAT:
                token = await self._stored_token(connector, settings, secret_store)
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="github_connector_provider_unknown",
                )
            return GitHubCredential(
                target=target,
                token=token,
                path_template=path_template,
                connector_id=connector.id,
                source=str(connector.provider_key),
            )

        if connector is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="github_connector_not_active"
            )

        target = github_settings_target(settings)
        if (
            not settings.github_legacy_token_enabled
            or target is None
            or settings.github_token is None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="deployment_connector_not_configured",
            )
        # One token for every tenant. Recorded on every use, so the day this
        # stops being a single-tenant pilot the audit log says exactly which
        # sites were still running on it.
        self.site_service._stage_event(
            "connector.legacy_token_used",
            "site",
            site_id,
            {
                "site_id": str(site_id),
                "connector_type": GITHUB_CONNECTOR,
                "repository": target.slug,
                "reason": "no_site_connector",
            },
        )
        return GitHubCredential(
            target=target,
            token=settings.github_token.get_secret_value(),
            path_template=settings.github_path_template,
            connector_id=None,
            source="legacy_settings",
        )

    async def _installation_token(
        self,
        connector: Connector,
        target: GitHubTarget,
        settings: Settings,
        client: httpx.AsyncClient,
    ) -> str:
        installation_id = (connector.config_json or {}).get("installation_id")
        app = await github_app_credential(self.session, settings, connector.tenant_id)
        if app is None or not isinstance(installation_id, int):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="github_app_not_configured"
            )
        # The installation belongs to whichever app it was created under, so
        # the token must be minted by that same app. Reading the connector's
        # tenant rather than the request context is deliberate: this is also
        # reached from the background sweeps, where there is no request.
        app_client = GitHubAppClient(client, app.app_id, app.private_key)
        try:
            minted = await app_client.mint_installation_token(installation_id)
        except GitHubAppError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
            ) from error
        if minted.repositories is not None and target.slug not in minted.repositories:
            # The tenant narrowed the installation after connecting. The grant
            # is theirs to change, and the connector has to follow it rather
            # than keep deploying to a repository they took back.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="github_repository_not_installed",
            )
        return minted.token

    async def _stored_token(
        self,
        connector: Connector,
        settings: Settings,
        secret_store: ConnectorSecretReader | None,
    ) -> str:
        if secret_store is None or not connector.secret_ref:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="github_connector_secret_missing"
            )
        try:
            payload = await secret_store.load(
                self.context.tenant_id, connector.id, connector.secret_ref
            )
        except (ValueError, TypeError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="github_connector_secret_invalid",
            ) from error
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="github_connector_secret_invalid",
            )
        return token


def sign_in_url(app: GitHubAppCredential, settings: Settings, state: str) -> str:
    query = urlencode(
        {
            "client_id": app.client_id,
            "redirect_uri": settings.github_app_callback_url,
            "state": state,
        }
    )
    return f"https://github.com/login/oauth/authorize?{query}"


def install_url(app_slug: str, state: str) -> str:
    return f"https://github.com/apps/{app_slug}/installations/new?{urlencode({'state': state})}"


@dataclass(frozen=True, slots=True)
class SignInOutcome:
    """Where the person goes next: back to GitHub, or to the picker for a site."""

    redirect_url: str | None = None
    site_id: UUID | None = None
    offered: int = 0


class GitHubSignInCallbackService:
    """Finishes a GitHub sign-in, on the request GitHub sends back.

    The request arrives with no session, so the tenant is derived from the
    state row and set as the scope before anything else is read or written --
    the same shape as the Google callback.

    It is reached two ways. After authorizing, with a `code`: the code is
    exchanged for the person's token, GitHub is asked which repositories they
    can push to, and that list -- not the token -- is what is kept. After
    installing or reconfiguring the app, with no code: the person is sent
    straight back through sign-in on the same state, because an installation
    id in a query string is not evidence of anything.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        app_factory: Callable[[UUID], Awaitable[GitHubAppCredential]],
        user_client_factory: Callable[[GitHubAppCredential], GitHubUserClient],
        settings: Settings,
    ) -> None:
        self.session = session
        self.app_factory = app_factory
        self.user_client_factory = user_client_factory
        self.settings = settings

    async def complete(self, state: str, code: str | None, trace_id: str) -> SignInOutcome:
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        unscoped = await self.session.scalar(
            select(ConnectorOauthState).where(ConnectorOauthState.state_hash == state_hash)
        )
        if unscoped is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="installation_state_invalid"
            )
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
            or oauth_state.requested_scopes != GITHUB_SIGN_IN
        ):
            # A state issued for Search Console, or for the retired
            # typed-repository install, cannot be redeemed here.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="installation_state_invalid"
            )
        app = await self.app_factory(oauth_state.tenant_id)
        if not app.can_sign_in:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="github_app_sign_in_not_configured",
            )

        config = dict(oauth_state.requested_config or {})
        bounces = int(config.get("bounces") or 0)
        if code is None:
            # Back from GitHub's install page. Sign in again on the same state;
            # GitHub does not ask twice, so to the person this is one redirect.
            if bounces >= MAX_SIGN_IN_BOUNCES:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="github_installation_not_found",
                )
            oauth_state.requested_config = {**config, "bounces": bounces + 1}
            return SignInOutcome(redirect_url=sign_in_url(app, self.settings, state))

        user = self.user_client_factory(app)
        try:
            token = await user.exchange_code(code)
            installations = await user.installations(token)
            if not installations:
                if bounces >= MAX_SIGN_IN_BOUNCES:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="github_installation_not_found",
                    )
                # Signed in, but the app is on none of their accounts yet.
                # This is the step where GitHub asks which repositories.
                oauth_state.requested_config = {**config, "bounces": bounces + 1}
                return SignInOutcome(
                    redirect_url=install_url(app.app_slug, state)
                )
            offered: list[dict[str, object]] = []
            for installation in installations:
                for repository in await user.pushable_repositories(
                    token, installation.id, installation.account
                ):
                    offered.append(
                        {
                            "id": repository.id,
                            "full_name": repository.full_name,
                            "default_branch": repository.default_branch,
                            "private": repository.private,
                            "installation_id": repository.installation_id,
                            "account": repository.account,
                        }
                    )
                    if len(offered) >= MAX_CHOICES:
                        break
        except GitHubAppError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error).split(":")[0]
            ) from error

        oauth_state.consumed_at = now
        offered.sort(key=lambda item: str(item["full_name"]).lower())
        self.session.add(
            ConnectorOauthState(
                tenant_id=oauth_state.tenant_id,
                site_id=oauth_state.site_id,
                connector_id=oauth_state.connector_id,
                # Never handed out: the picker finds this row by site and actor.
                state_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                requested_scopes=list(GITHUB_CHOICE),
                requested_config={
                    "repositories": offered,
                    # Where each account's repository choice is changed on
                    # GitHub. Built from GitHub's answer, never from input.
                    "configure": [
                        {"account": item.account, "url": item.configure_url}
                        for item in installations
                    ],
                },
                expires_at=now + CHOICE_TTL,
                created_by=oauth_state.created_by,
            )
        )
        payload = {
            "connector_id": str(oauth_state.connector_id),
            "site_id": str(oauth_state.site_id),
            "connector_type": GITHUB_CONNECTOR,
            "provider_key": PROVIDER_APP,
            "installations": len(installations),
            "repositories_offered": len(offered),
        }
        self.session.add(
            AuditEvent(
                tenant_id=oauth_state.tenant_id,
                actor_type="user",
                actor_id=str(oauth_state.created_by),
                action="connector.github_signed_in",
                resource_type="connector",
                resource_id=str(oauth_state.connector_id),
                trace_id=trace_id,
                metadata_json=payload,
                event_hash=stable_hash(payload),
            )
        )
        return SignInOutcome(site_id=oauth_state.site_id, offered=len(offered))


def envelope_reader(settings: Settings, session: AsyncSession) -> ConnectorSecretReader | None:
    """The store that can decrypt a stored token, when one is configured."""
    if (
        settings.connector_secret_backend != "database_envelope"
        or not settings.connector_secret_encryption_key
    ):
        return None
    from app.services.connector_secrets import DatabaseEnvelopeSecretStore, decode_encryption_key

    return DatabaseEnvelopeSecretStore(
        session,
        decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value()),
        settings.connector_secret_key_version,
    )


async def credential_for_site(
    session: AsyncSession,
    context: TenantContext,
    site_id: UUID,
    settings: Settings,
    client: httpx.AsyncClient,
) -> GitHubCredential:
    """The one entry point every deployment path uses."""
    return await GitHubConnectorService(session, context).resolve(
        site_id, settings, client, envelope_reader(settings, session)
    )


async def _site_id_of(
    session: AsyncSession, context: TenantContext, model: type, record_id: UUID, missing: str
) -> UUID:
    site_id = await session.scalar(
        select(model.site_id).where(  # type: ignore[attr-defined]
            model.id == record_id,  # type: ignore[attr-defined]
            model.tenant_id == context.tenant_id,  # type: ignore[attr-defined]
        )
    )
    if site_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=missing)
    return site_id


async def credential_for_proposal(
    session: AsyncSession,
    context: TenantContext,
    proposal_id: UUID,
    settings: Settings,
    client: httpx.AsyncClient,
) -> GitHubCredential:
    """A proposal deploys with its own site's connector, never another's."""
    from app.db.models import Proposal

    site_id = await _site_id_of(session, context, Proposal, proposal_id, "proposal_not_found")
    return await credential_for_site(session, context, site_id, settings, client)


async def credential_for_opportunity(
    session: AsyncSession,
    context: TenantContext,
    opportunity_id: UUID,
    settings: Settings,
    client: httpx.AsyncClient,
) -> GitHubCredential:
    from app.db.models import Opportunity

    site_id = await _site_id_of(
        session, context, Opportunity, opportunity_id, "opportunity_not_found"
    )
    return await credential_for_site(session, context, site_id, settings, client)
