"""Renewing a Google token with the client that issued it.

A refresh token is redeemable only by the OAuth client it was issued to. The
worker used to build one refresher at startup from `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` and use it for every tenant, which was correct for
exactly as long as there was only one client.

It is worse than merely wrong once tenants bring their own. Google answers a
refresh presented by the wrong client with `invalid_grant`, and `invalid_grant`
is precisely the code the refresher treats as "the grant is gone, send a human
to re-consent". So the tenant would be sent round the consent screen, get a
fresh grant against their own client, and be sent round again an hour later,
for ever, with nothing in the logs saying the server was using somebody else's
client id.

So the client is resolved per tenant, from the same `tenant_credential` row the
API writes, and the environment pair is the fallback for the tenants that
predate per-tenant credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.connectors.google_oauth import GoogleTokenHttpRefresher, TokenRefresher

GOOGLE_OAUTH_CLIENT = "google_oauth_client"


def credential_aad(tenant_id: UUID, provider: str, key_version: str) -> bytes:
    """Must match `api/app/services/tenant_credentials.credential_aad` exactly.

    Two processes seal and open the same rows. If these ever disagree the
    symptom is a decryption failure on a row that is perfectly intact, so the
    duplication is deliberate and worth more than a shared import that would
    couple the worker's dependency set to the API's.
    """
    return f"{tenant_id}:tenant-credential:{provider}:{key_version}".encode()


async def load_google_client(
    pool: asyncpg.Pool, tenant_id: UUID, encryption_key: bytes
) -> tuple[str, str] | None:
    """This tenant's Google client id and secret, or None if they set none."""
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        row = await connection.fetchrow(
            """
            SELECT config_json,ciphertext,nonce,aad_hash,key_version
            FROM tenant_credential
            WHERE tenant_id=$1 AND provider=$2 AND revoked_at IS NULL
            """,
            tenant_id,
            GOOGLE_OAUTH_CLIENT,
        )
    if row is None:
        return None
    aad = credential_aad(tenant_id, GOOGLE_OAUTH_CLIENT, row["key_version"])
    if not hmac.compare_digest(hashlib.sha256(aad).hexdigest(), row["aad_hash"]):
        raise ValueError("tenant_credential_aad_mismatch")
    try:
        plaintext = AESGCM(encryption_key).decrypt(
            bytes(row["nonce"]), bytes(row["ciphertext"]), aad
        )
        secret = json.loads(plaintext)
    except Exception as error:
        raise ValueError("tenant_credential_invalid") from error
    config: Any = row["config_json"]
    if isinstance(config, str):
        # asyncpg hands jsonb back as text unless a codec is registered.
        config = json.loads(config)
    if not isinstance(config, dict) or not isinstance(secret, dict):
        raise TypeError("tenant_credential_invalid")
    client_id = config.get("client_id")
    client_secret = secret.get("client_secret")
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        return None
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


class TenantTokenRefresherFactory:
    """Builds -- and remembers -- one refresher per tenant.

    Cached because a sync renews repeatedly while it walks a date range, and
    reading and decrypting the same row for each renewal would be pure cost.
    The cache is per process and per tenant; a rotated credential is picked up
    when the worker restarts or the entry is dropped after a refusal.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        client: httpx.AsyncClient,
        *,
        encryption_key: bytes | None,
        fallback: TokenRefresher | None,
    ) -> None:
        self._pool = pool
        self._client = client
        self._encryption_key = encryption_key
        self._fallback = fallback
        self._cache: dict[UUID, TokenRefresher | None] = {}

    async def for_tenant(self, tenant_id: UUID) -> TokenRefresher | None:
        if tenant_id in self._cache:
            return self._cache[tenant_id]
        refresher: TokenRefresher | None = self._fallback
        if self._encryption_key is not None:
            pair = await load_google_client(self._pool, tenant_id, self._encryption_key)
            if pair is not None:
                client_id, client_secret = pair
                refresher = GoogleTokenHttpRefresher(
                    self._client, client_id=client_id, client_secret=client_secret
                )
        self._cache[tenant_id] = refresher
        return refresher

    def forget(self, tenant_id: UUID) -> None:
        """Drop a cached refresher, so a rotation is picked up on the next sync."""
        self._cache.pop(tenant_id, None)
