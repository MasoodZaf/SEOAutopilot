"""A tenant's own provider credentials, sealed at rest.

Every tenant used to borrow one Google OAuth client and one GitHub App from the
process environment. For an estate we own that is merely convenient; for anyone
else it is wrong in three separate ways. Their consent screen names our
application rather than theirs. Their API calls spend our project's quota, so
one tenant's crawl can rate-limit another's. And access is all-or-nothing:
revoking one tenant means revoking every tenant, because there is only one
credential to revoke.

So a credential a tenant provides is a row a tenant owns, scoped by the same
row-level security as the rest of their data and sealed with the same envelope
as connector secrets. Two things are worth stating plainly about what that does
and does not buy:

* The deployment can decrypt it. It has to -- the server is what exchanges the
  authorization code, so the client secret must be usable by this process. What
  the envelope protects against is a database read: a dump, a backup, a replica,
  an injection that returns rows. It is not protection from the operator, and
  the operational document says so in those words.
* The additional authenticated data binds tenant, provider and key version, so
  a ciphertext lifted from one tenant's row and written into another's does not
  decrypt. Moving the row is not a way to use somebody else's credential.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import AppUser, AuditEvent, TenantCredential
from app.services.connector_secrets import decode_encryption_key
from app.services.google_client_check import REFUSAL_DETAIL, GoogleClientProbe
from app.services.sites import stable_hash

GOOGLE_OAUTH_CLIENT = "google_oauth_client"
GITHUB_APP = "github_app"
SUPPORTED_PROVIDERS = frozenset({GOOGLE_OAUTH_CLIENT, GITHUB_APP})


def credential_aad(tenant_id: UUID, provider: str, key_version: str) -> bytes:
    """What the ciphertext is bound to.

    Deliberately a different shape from `connector_secrets.secret_aad`, which
    carries a connector id in the same position. A tenant credential and a
    connector secret must not be interchangeable even under one key.
    """
    return f"{tenant_id}:tenant-credential:{provider}:{key_version}".encode()


class TenantCredentialStore:
    """Reads and writes one tenant's provider credentials."""

    def __init__(self, session: AsyncSession, key: bytes, key_version: str) -> None:
        if len(key) != 32:
            raise ValueError("invalid_tenant_credential_key")
        self.session = session
        self.key = key
        self.key_version = key_version

    async def put(
        self,
        tenant_id: UUID,
        provider: str,
        *,
        config: dict[str, Any],
        secret: dict[str, Any],
        created_by: UUID | None,
    ) -> TenantCredential:
        """Store a credential, superseding whatever this tenant had before.

        Rotation revokes rather than overwrites, so an authorization already in
        flight against the old client can still be explained afterwards instead
        of failing against a row that no longer exists.
        """
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError("unsupported_tenant_credential_provider")
        now = datetime.now(UTC)
        existing = await self._live(tenant_id, provider)
        if existing is not None:
            existing.revoked_at = now

        plaintext = json.dumps(secret, sort_keys=True, separators=(",", ":")).encode()
        nonce = secrets.token_bytes(12)
        aad = credential_aad(tenant_id, provider, self.key_version)
        ciphertext = AESGCM(self.key).encrypt(nonce, plaintext, aad)

        credential = TenantCredential(
            tenant_id=tenant_id,
            provider=provider,
            config_json=config,
            ciphertext=ciphertext,
            nonce=nonce,
            aad_hash=hashlib.sha256(aad).hexdigest(),
            key_version=self.key_version,
            created_by=created_by,
        )
        self.session.add(credential)
        await self.session.flush()
        return credential

    async def get(self, tenant_id: UUID, provider: str) -> dict[str, Any] | None:
        """The decrypted secret half, or None when this tenant has not set one."""
        credential = await self._live(tenant_id, provider)
        if credential is None:
            return None
        return self._open(credential)

    async def describe(self, tenant_id: UUID, provider: str) -> TenantCredential | None:
        """The row without decrypting it, for listing what is configured."""
        return await self._live(tenant_id, provider)

    async def get_pair(
        self, tenant_id: UUID, provider: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Both halves at once: the clear config and the decrypted secret.

        Every caller that actually *uses* a credential needs both -- a client
        id is in the config and its secret is not -- and fetching them
        separately would read the row twice and could straddle a rotation.
        """
        credential = await self._live(tenant_id, provider)
        if credential is None:
            return None
        return dict(credential.config_json), self._open(credential)

    async def revoke(self, tenant_id: UUID, provider: str) -> bool:
        credential = await self._live(tenant_id, provider)
        if credential is None:
            return False
        credential.revoked_at = datetime.now(UTC)
        await self.session.flush()
        return True

    def _open(self, credential: TenantCredential) -> dict[str, Any]:
        aad = credential_aad(credential.tenant_id, credential.provider, credential.key_version)
        if not secrets.compare_digest(hashlib.sha256(aad).hexdigest(), credential.aad_hash):
            # The row was moved between tenants or providers. Decrypting would
            # fail anyway; refusing here says why.
            raise ValueError("tenant_credential_aad_mismatch")
        plaintext = AESGCM(self.key).decrypt(credential.nonce, credential.ciphertext, aad)
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise TypeError("tenant_credential_invalid")
        return {str(key): item for key, item in value.items()}

    async def _live(self, tenant_id: UUID, provider: str) -> TenantCredential | None:
        return await self.session.scalar(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
                TenantCredential.revoked_at.is_(None),
            )
        )


def store_for(session: AsyncSession, settings: Settings) -> TenantCredentialStore:
    """The store this deployment is configured for, or a refusal that says why."""
    if settings.connector_secret_backend != "database_envelope":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tenant_credentials_not_configured",
        )
    if settings.connector_secret_encryption_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tenant_credentials_not_configured",
        )
    return TenantCredentialStore(
        session,
        decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value()),
        settings.connector_secret_key_version,
    )


class GoogleOAuthClient:
    """The three values a Google authorization needs, from wherever they came."""

    __slots__ = ("client_id", "client_secret", "source")

    def __init__(self, client_id: str, client_secret: str, source: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        # "tenant" or "platform". Reported to the UI so somebody can tell at a
        # glance whose Google project a consent screen will name.
        self.source = source


async def google_oauth_client(
    session: AsyncSession, settings: Settings, tenant_id: UUID
) -> GoogleOAuthClient | None:
    """This tenant's Google OAuth client, falling back to the deployment's.

    The fallback is what keeps the existing estate working: the tenants that
    predate this were set up against the platform client and would otherwise
    lose their connectors the moment this shipped. A tenant created from now on
    has no platform credential to fall back to unless an operator deliberately
    left one configured, which the settings page states out loud.
    """
    if settings.connector_secret_backend == "database_envelope" and (
        settings.connector_secret_encryption_key is not None
    ):
        store = TenantCredentialStore(
            session,
            decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value()),
            settings.connector_secret_key_version,
        )
        pair = await store.get_pair(tenant_id, GOOGLE_OAUTH_CLIENT)
        if pair is not None:
            config, secret = pair
            client_id = str(config.get("client_id", ""))
            client_secret = str(secret.get("client_secret", ""))
            if client_id and client_secret:
                return GoogleOAuthClient(client_id, client_secret, "tenant")
    if settings.google_client_id and settings.google_client_secret:
        return GoogleOAuthClient(
            settings.google_client_id,
            settings.google_client_secret.get_secret_value(),
            "platform",
        )
    return None


class GitHubAppCredential:
    """What a GitHub App install, token exchange and sign-in need.

    The client pair is the app's OAuth half. It is optional because an app
    saved before sign-in existed has none; the connect flow refuses with a
    reason rather than falling back to trusting an installation id.
    """

    __slots__ = ("app_id", "app_slug", "client_id", "client_secret", "private_key", "source")

    def __init__(
        self,
        app_id: str,
        app_slug: str,
        private_key: str,
        source: str,
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        self.app_id = app_id
        self.app_slug = app_slug
        self.private_key = private_key
        self.source = source
        self.client_id = client_id
        self.client_secret = client_secret

    @property
    def can_sign_in(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _platform_github_app(settings: Settings) -> GitHubAppCredential | None:
    if not settings.github_app_configured:
        return None
    assert settings.github_app_id is not None
    assert settings.github_app_slug is not None
    assert settings.github_app_private_key is not None
    return GitHubAppCredential(
        settings.github_app_id,
        settings.github_app_slug,
        settings.github_app_private_key.get_secret_value(),
        "platform",
        settings.github_app_client_id or "",
        settings.github_app_client_secret.get_secret_value()
        if settings.github_app_client_secret
        else "",
    )


async def github_app_credential(
    session: AsyncSession, settings: Settings, tenant_id: UUID
) -> GitHubAppCredential | None:
    """This tenant's GitHub App, falling back to the deployment's.

    Same fallback, same reason, as `google_oauth_client`: the estate that
    predates this was installed against the platform app, and pulling it out
    from under those tenants would break every deployment they have.
    """
    platform = _platform_github_app(settings)
    if settings.connector_secret_backend == "database_envelope" and (
        settings.connector_secret_encryption_key is not None
    ):
        store = TenantCredentialStore(
            session,
            decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value()),
            settings.connector_secret_key_version,
        )
        pair = await store.get_pair(tenant_id, GITHUB_APP)
        if pair is not None:
            config, secret = pair
            app_id = str(config.get("app_id", ""))
            app_slug = str(config.get("app_slug", ""))
            private_key = str(secret.get("private_key", ""))
            client_id = str(config.get("client_id", ""))
            client_secret = str(secret.get("client_secret", ""))
            if (
                not client_id
                and platform is not None
                and platform.app_id == app_id
                and platform.can_sign_in
            ):
                # The workspace saved the deployment's own app under Keys
                # before sign-in existed. It is the same app, so its OAuth
                # half is the same too -- borrowing it is not borrowing
                # anybody else's credential.
                client_id, client_secret = platform.client_id, platform.client_secret
            if app_id and app_slug and private_key:
                return GitHubAppCredential(
                    app_id, app_slug, private_key, "tenant", client_id, client_secret
                )
    return platform


class TenantCredentialService:
    """The role-checked, audited front door to the store."""

    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def _creator(self) -> UUID | None:
        """The acting user, but only if they are one this database knows.

        `created_by` is a foreign key to `app_user`, and it is provenance
        rather than authorization -- nothing reads it to decide anything. The
        development pilot context carries a fixed actor id that has no
        `app_user` row behind it, so naming it here made the whole insert fail
        on a foreign key: storing a credential returned 500 while the audit
        row, which holds the same id as plain text, was written happily.

        Recording nobody is the honest answer when we cannot name somebody, and
        a better one than refusing to store the credential at all.
        """
        exists = await self.session.scalar(
            select(AppUser.id).where(AppUser.id == self.context.actor_id)
        )
        return exists

    async def upsert_google_client(
        self,
        store: TenantCredentialStore,
        client_id: str,
        client_secret: str,
        probe: GoogleClientProbe | None = None,
    ) -> TenantCredential:
        # Only an owner or admin. A Google client secret is the credential the
        # whole tenant's Search Console and Analytics access hangs from.
        self.context.require(Role.OWNER, Role.ADMIN)
        client_id = client_id.strip()
        client_secret = client_secret.strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            # Cheap, but it catches the common paste error -- the project
            # number, or the API key from the next panel along -- before the
            # tenant is sent to Google to be told something unhelpful.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="google_client_id_invalid",
            )
        if len(client_secret) < 8:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="google_client_secret_invalid",
            )
        # The shape was right and the client still may not work, because the
        # part that decides lives in the tenant's own Google Cloud project. So
        # ask Google before storing it. Refusing a credential that cannot work
        # is kinder than accepting one and letting them discover it days later
        # on a Google error page we never see -- which is exactly how this was
        # found, by a tester who had to send us a photograph of his screen.
        if probe is not None:
            result = await probe(client_id)
            if result.blocking:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=REFUSAL_DETAIL[result.status],
                )
        credential = await store.put(
            self.context.tenant_id,
            GOOGLE_OAUTH_CLIENT,
            config={"client_id": client_id},
            secret={"client_secret": client_secret},
            created_by=await self._creator(),
        )
        self._audit("tenant_credential.stored", credential.id, {"provider": GOOGLE_OAUTH_CLIENT})
        return credential

    async def upsert_github_app(
        self,
        store: TenantCredentialStore,
        app_id: str,
        app_slug: str,
        private_key: str,
        client_id: str = "",
        client_secret: str = "",
    ) -> TenantCredential:
        self.context.require(Role.OWNER, Role.ADMIN)
        app_id = app_id.strip()
        app_slug = app_slug.strip()
        private_key = private_key.strip()
        client_id = client_id.strip()
        client_secret = client_secret.strip()
        if bool(client_id) != bool(client_secret):
            # Half an OAuth client cannot sign anybody in, and would be found
            # out only when a person is already on GitHub's page.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="github_app_client_incomplete",
            )
        if not app_id.isdigit():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="github_app_id_invalid"
            )
        if not app_slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="github_app_slug_invalid"
            )
        from app.services.github_app import load_private_key

        try:
            load_private_key(private_key)
        except Exception as error:
            # Parsed now rather than at the first installation attempt, which
            # is after the tenant has already granted repository access.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="github_app_private_key_invalid",
            ) from error
        credential = await store.put(
            self.context.tenant_id,
            GITHUB_APP,
            config={
                "app_id": app_id,
                "app_slug": app_slug,
                **({"client_id": client_id} if client_id else {}),
            },
            secret={
                "private_key": private_key,
                **({"client_secret": client_secret} if client_secret else {}),
            },
            created_by=await self._creator(),
        )
        self._audit("tenant_credential.stored", credential.id, {"provider": GITHUB_APP})
        return credential

    async def revoke(self, store: TenantCredentialStore, provider: str) -> bool:
        self.context.require(Role.OWNER, Role.ADMIN)
        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="tenant_credential_not_found"
            )
        removed = await store.revoke(self.context.tenant_id, provider)
        if removed:
            self._audit("tenant_credential.revoked", None, {"provider": provider})
        return removed

    def _audit(self, action: str, resource_id: UUID | None, payload: dict[str, str]) -> None:
        body: dict[str, object] = {**payload, "tenant_id": str(self.context.tenant_id)}
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action=action,
                resource_type="tenant_credential",
                resource_id=str(resource_id) if resource_id else str(self.context.tenant_id),
                trace_id=self.context.trace_id,
                metadata_json=body,
                event_hash=stable_hash(body),
            )
        )
