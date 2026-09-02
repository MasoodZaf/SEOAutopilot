import hashlib
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NotificationChannelCreate
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, NotificationChannel, Site
from app.services.opportunities import stable_hash

MANAGE_CHANNEL_ROLES = {Role.OWNER, Role.ADMIN}
MAX_CHANNELS_PER_TENANT = 20


def channel_aad(tenant_id: UUID, channel_kind: str, key_version: str) -> bytes:
    return f"{tenant_id}:notification:{channel_kind}:{key_version}".encode()


def destination_hint(url: str) -> str:
    """Host plus a truncated path. Never enough to replay the webhook."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "unknown").lower()
    tail = parsed.path.rsplit("/", 1)[-1]
    suffix = f"/…{tail[-4:]}" if len(tail) >= 4 else ""
    return f"{host}{suffix}"[:200]


class NotificationChannelService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        encryption_key: bytes,
        key_version: str,
    ) -> None:
        if len(encryption_key) != 32:
            raise ValueError("invalid_notification_secret_key")
        self.session = session
        self.context = context
        self.encryption_key = encryption_key
        self.key_version = key_version

    async def list_channels(self) -> list[NotificationChannel]:
        result = await self.session.scalars(
            select(NotificationChannel)
            .where(
                NotificationChannel.tenant_id == self.context.tenant_id,
                NotificationChannel.revoked_at.is_(None),
            )
            .order_by(NotificationChannel.created_at.desc(), NotificationChannel.id.desc())
        )
        return list(result)

    async def create(self, command: NotificationChannelCreate) -> NotificationChannel:
        if self.context.role not in MANAGE_CHANNEL_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_notification_channel",
            )
        if command.site_id is not None:
            site = await self.session.scalar(
                select(Site.id).where(
                    Site.id == command.site_id, Site.tenant_id == self.context.tenant_id
                )
            )
            if site is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        existing = await self.list_channels()
        if len(existing) >= MAX_CHANNELS_PER_TENANT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="notification_channel_limit_reached"
            )

        url = command.webhook_url.get_secret_value().strip()
        nonce = secrets.token_bytes(12)
        aad = channel_aad(self.context.tenant_id, command.kind.value, self.key_version)
        ciphertext = AESGCM(self.encryption_key).encrypt(nonce, url.encode(), aad)

        channel = NotificationChannel(
            tenant_id=self.context.tenant_id,
            site_id=command.site_id,
            kind=command.kind.value,
            name=command.name,
            destination_hint=destination_hint(url),
            ciphertext=ciphertext,
            nonce=nonce,
            aad_hash=hashlib.sha256(aad).hexdigest(),
            key_version=self.key_version,
            created_by=self.context.actor_id,
        )
        self.session.add(channel)
        await self.session.flush()

        payload = {
            "kind": channel.kind,
            "name": channel.name,
            "destination_hint": channel.destination_hint,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="notification_channel.created",
                resource_type="notification_channel",
                resource_id=str(channel.id),
                trace_id=self.context.trace_id,
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        await self.session.refresh(channel)
        return channel

    async def revoke(self, channel_id: UUID) -> NotificationChannel:
        if self.context.role not in MANAGE_CHANNEL_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_notification_channel",
            )
        channel = await self.session.scalar(
            select(NotificationChannel).where(
                NotificationChannel.id == channel_id,
                NotificationChannel.tenant_id == self.context.tenant_id,
                NotificationChannel.revoked_at.is_(None),
            )
        )
        if channel is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="notification_channel_not_found"
            )
        channel.revoked_at = datetime.now(UTC)
        channel.enabled = False
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="notification_channel.revoked",
                resource_type="notification_channel",
                resource_id=str(channel.id),
                trace_id=self.context.trace_id,
                metadata_json={"kind": channel.kind},
                event_hash=stable_hash(
                    {"kind": channel.kind, "actor_id": str(self.context.actor_id)}
                ),
            )
        )
        await self.session.flush()
        await self.session.refresh(channel)
        return channel
