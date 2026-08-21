from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConnectorSecret


class ConnectorSecretStore(Protocol):
    async def store(
        self, tenant_id: UUID, connector_id: UUID, provider: str, payload: dict[str, object]
    ) -> str: ...


def decode_encryption_key(encoded: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("CONNECTOR_SECRET_ENCRYPTION_KEY must be URL-safe base64") from error
    if len(key) != 32:
        raise ValueError("CONNECTOR_SECRET_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def secret_aad(tenant_id: UUID, connector_id: UUID, provider: str, key_version: str) -> bytes:
    return f"{tenant_id}:{connector_id}:{provider}:{key_version}".encode()


class DatabaseEnvelopeSecretStore:
    """Local/test adapter. Production is rejected by Settings validation."""

    def __init__(self, session: AsyncSession, key: bytes, key_version: str) -> None:
        if len(key) != 32:
            raise ValueError("invalid_connector_secret_key")
        self.session = session
        self.key = key
        self.key_version = key_version

    async def store(
        self, tenant_id: UUID, connector_id: UUID, provider: str, payload: dict[str, object]
    ) -> str:
        plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        nonce = secrets.token_bytes(12)
        aad = secret_aad(tenant_id, connector_id, provider, self.key_version)
        ciphertext = AESGCM(self.key).encrypt(nonce, plaintext, aad)
        now = datetime.now(UTC)
        await self.session.execute(
            update(ConnectorSecret)
            .where(
                ConnectorSecret.tenant_id == tenant_id,
                ConnectorSecret.connector_id == connector_id,
                ConnectorSecret.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        secret = ConnectorSecret(
            tenant_id=tenant_id,
            connector_id=connector_id,
            provider=provider,
            ciphertext=ciphertext,
            nonce=nonce,
            aad_hash=hashlib.sha256(aad).hexdigest(),
            key_version=self.key_version,
        )
        self.session.add(secret)
        await self.session.flush()
        return f"db-envelope://{secret.id}"

    async def load(
        self, tenant_id: UUID, connector_id: UUID, secret_ref: str
    ) -> dict[str, object]:
        prefix = "db-envelope://"
        if not secret_ref.startswith(prefix):
            raise ValueError("unsupported_secret_ref")
        try:
            secret_id = UUID(secret_ref.removeprefix(prefix))
        except ValueError as error:
            raise ValueError("invalid_secret_ref") from error
        secret = await self.session.scalar(
            select(ConnectorSecret).where(
                ConnectorSecret.id == secret_id,
                ConnectorSecret.tenant_id == tenant_id,
                ConnectorSecret.connector_id == connector_id,
                ConnectorSecret.revoked_at.is_(None),
            )
        )
        if secret is None:
            raise ValueError("connector_secret_not_found")
        aad = secret_aad(tenant_id, connector_id, secret.provider, secret.key_version)
        if not secrets.compare_digest(hashlib.sha256(aad).hexdigest(), secret.aad_hash):
            raise ValueError("connector_secret_aad_mismatch")
        plaintext = AESGCM(self.key).decrypt(secret.nonce, secret.ciphertext, aad)
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise TypeError("connector_secret_invalid")
        return {str(key): item for key, item in value.items()}
