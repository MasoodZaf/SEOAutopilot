"""What every connector sync does the same way, written once.

Search Console and GA4 differ only in the provider they call and the rows they
write. Everything around that -- claiming the `connector_sync` row under a
lease, reading the grant out of its envelope, renewing an access token that
lives an hour, checkpointing progress so a long run can resume, and closing the
row out with counts or a safe error code -- is the same work, and it is the work
where a mistake costs a tenant their data or their isolation.

So it lives here rather than once per provider. Two things are parameterised,
and both of them deliberately:

`connector_type` is part of the claim statement itself, not a check afterwards.
A GA4 sync row and a Search Console sync row arrive on the same stream and are
offered to both consumers; each claims only its own kind, and the other sees no
row at all rather than a row it must remember to reject.

`expected_scopes` is checked against what the envelope actually holds. A
credential is not usable by a sync merely because it belongs to the connector:
it has to carry the grant that sync needs. Reading GA4 with a Search Console
token would fail at Google anyway, but it would fail as `authorization_required`
and send a tenant to a consent screen for a bug on this side.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets as secrets_module
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.connectors.google_oauth import GoogleAuthorizationRevoked, TokenRefresher

STREAM = "seo-autopilot:events"
SECRET_PREFIX = "db-envelope://"

# Renew before Google would refuse rather than after. A token that expires
# between this check and the request it authorises fails a whole day's page for
# no reason a person could act on.
TOKEN_RENEWAL_MARGIN = timedelta(minutes=10)

LEASE_INTERVAL = "5 minutes"


class SyncStream(Protocol):
    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> bool: ...
    async def xautoclaim(
        self, name: str, groupname: str, consumername: str, **kwargs: Any
    ) -> Any: ...
    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        **kwargs: Any,
    ) -> Any: ...
    async def xack(self, name: str, groupname: str, *ids: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ClaimedSync:
    """One leased sync row, with the connector facts its provider will need.

    The cursor is carried as it was stored rather than parsed. Its shape belongs
    to the provider that wrote it -- Search Console pages by row offset within a
    day, GA4 by report offset -- and a shared claim has no business deciding
    which of those a row means.
    """

    id: UUID
    tenant_id: UUID
    connector_id: UUID
    site_id: UUID
    property_ref: str
    secret_ref: str
    range_start: date
    range_end: date
    raw_cursor: object
    base_days_completed: int
    base_rows_seen: int
    base_rows_upserted: int
    base_rows_new: int


def decode_encryption_key(encoded: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("invalid_connector_secret_key") from error
    if len(key) != 32:
        raise ValueError("invalid_connector_secret_key")
    return key


def require_secret_bytes(value: str | None, error_code: str) -> bytes:
    if value is None:
        raise ValueError(error_code)
    encoded = value.encode()
    if len(encoded) < 32:
        raise ValueError(error_code)
    return encoded


def secret_aad(tenant_id: UUID, connector_id: UUID, provider: str, key_version: str) -> bytes:
    return f"{tenant_id}:{connector_id}:{provider}:{key_version}".encode()


def decrypt_secret_payload(
    *,
    tenant_id: UUID,
    connector_id: UUID,
    provider: str,
    key_version: str,
    ciphertext: bytes,
    nonce: bytes,
    aad_hash: str,
    encryption_key: bytes,
) -> dict[str, object]:
    aad = secret_aad(tenant_id, connector_id, provider, key_version)
    if not hmac.compare_digest(hashlib.sha256(aad).hexdigest(), aad_hash):
        raise ValueError("connector_secret_aad_mismatch")
    try:
        plaintext = AESGCM(encryption_key).decrypt(nonce, ciphertext, aad)
        payload = json.loads(plaintext)
    except Exception as error:
        raise ValueError("connector_secret_invalid") from error
    if not isinstance(payload, dict):
        raise TypeError("connector_secret_invalid")
    return {str(key): value for key, value in payload.items()}


def parse_day_offset_cursor(value: object, *, offset_field: str) -> tuple[date, int] | None:
    """A resume point of `(day, offset)`, however the provider names the offset.

    Both providers walk a date range one day at a time and page within a day.
    They disagree only on what the page pointer is called, so the field name is
    the caller's to supply and everything else -- the fail-closed parsing, the
    tolerance for a driver that hands back JSON as text -- is shared.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("invalid_sync_cursor") from error
    if value in ({}, None):
        return None
    if not isinstance(value, Mapping):
        raise TypeError("invalid_sync_cursor")
    raw_day = value.get("day")
    raw_offset = value.get(offset_field, 0)
    if not isinstance(raw_day, str) or not isinstance(raw_offset, int) or isinstance(raw_offset, bool):
        raise TypeError("invalid_sync_cursor")
    try:
        return date.fromisoformat(raw_day), raw_offset
    except ValueError as error:
        raise ValueError("invalid_sync_cursor") from error


async def set_tenant(connection: Any, tenant_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))


async def claim_sync(
    pool: asyncpg.Pool, tenant_id: UUID, sync_id: UUID, *, connector_type: str
) -> ClaimedSync | None:
    """Lease this sync row, but only if it belongs to this kind of connector.

    Both consumers read the same stream and are offered the same
    `connector.sync_requested` message. The type predicate is what makes that
    safe: a GA4 row simply does not match the Search Console consumer's claim,
    so it goes back unclaimed for the consumer that can do the work.
    """
    async with pool.acquire() as connection, connection.transaction():
        await set_tenant(connection, tenant_id)
        row = await connection.fetchrow(
            f"""
            UPDATE connector_sync cs
            SET status='running', started_at=coalesce(started_at,now()),
                lease_until=now()+interval '{LEASE_INTERVAL}', error_code=null
            FROM connector c, site s
            WHERE cs.id=$1 AND cs.tenant_id=$2
              AND c.id=cs.connector_id AND c.tenant_id=cs.tenant_id
              AND s.id=c.site_id AND s.tenant_id=c.tenant_id
              AND c.status='active' AND c.type=$3
              AND c.secret_ref IS NOT NULL
              AND (cs.status='queued' OR (cs.status='running' AND cs.lease_until<now()))
            RETURNING cs.id,cs.tenant_id,cs.connector_id,c.site_id,
              c.external_account_ref,c.secret_ref,cs.range_start,cs.range_end,
              cs.cursor_json,cs.counts_json
            """,
            sync_id,
            tenant_id,
            connector_type,
        )
    if row is None:
        return None
    raw_counts = row["counts_json"]
    if isinstance(raw_counts, str):
        try:
            raw_counts = json.loads(raw_counts)
        except json.JSONDecodeError as error:
            raise ValueError("invalid_sync_counts") from error
    counts = raw_counts if isinstance(raw_counts, Mapping) else {}
    return ClaimedSync(
        id=row["id"],
        tenant_id=row["tenant_id"],
        connector_id=row["connector_id"],
        site_id=row["site_id"],
        property_ref=row["external_account_ref"],
        secret_ref=row["secret_ref"],
        range_start=row["range_start"],
        range_end=row["range_end"],
        raw_cursor=row["cursor_json"],
        base_days_completed=int(counts.get("days_completed", 0)),
        base_rows_seen=int(counts.get("rows_seen", 0)),
        base_rows_upserted=int(counts.get("rows_upserted", 0)),
        # Absent on every sync recorded before this key existed. Reading it as
        # 0 is right: those rows counted inserts in `rows_upserted`, so the new
        # column starts empty rather than inheriting a number that meant
        # something else.
        base_rows_new=int(counts.get("rows_new", 0)),
    )


@dataclass(frozen=True, slots=True)
class StoredCredential:
    """The whole grant as it sits in the envelope, not just the usable half."""

    secret_id: UUID
    provider: str
    key_version: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: list[str]

    def is_fresh(self, now: datetime) -> bool:
        return self.expires_at - now > TOKEN_RENEWAL_MARGIN


async def load_credential(
    pool: asyncpg.Pool,
    sync: ClaimedSync,
    encryption_key: bytes,
    *,
    expected_scopes: frozenset[str],
) -> StoredCredential:
    """Read the grant. Deliberately does not judge whether it has expired.

    Expiry used to be fatal here, which is what made Search Console data
    impossible to accumulate: an access token lives an hour, so every sync after
    the first hour refused and sent the tenant back to a consent screen. Expiry
    is now a reason to renew, and only a refusal from Google is a reason to ask
    a person for anything.

    The scope set is judged, though. It has to be exactly the one this sync was
    built to use: a credential that grants something else is not a narrower
    version of the right one, it is the wrong one.
    """
    if not sync.secret_ref.startswith(SECRET_PREFIX):
        raise ValueError("unsupported_secret_ref")
    try:
        secret_id = UUID(sync.secret_ref.removeprefix(SECRET_PREFIX))
    except ValueError as error:
        raise ValueError("invalid_secret_ref") from error
    async with pool.acquire() as connection, connection.transaction():
        await set_tenant(connection, sync.tenant_id)
        row = await connection.fetchrow(
            """
            SELECT provider,ciphertext,nonce,aad_hash,key_version
            FROM connector_secret
            WHERE id=$1 AND tenant_id=$2 AND connector_id=$3 AND revoked_at IS NULL
            """,
            secret_id,
            sync.tenant_id,
            sync.connector_id,
        )
    if row is None:
        raise ValueError("connector_secret_not_found")
    payload = decrypt_secret_payload(
        tenant_id=sync.tenant_id,
        connector_id=sync.connector_id,
        provider=row["provider"],
        key_version=row["key_version"],
        ciphertext=bytes(row["ciphertext"]),
        nonce=bytes(row["nonce"]),
        aad_hash=row["aad_hash"],
        encryption_key=encryption_key,
    )
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    scopes = payload.get("scopes")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(scopes, list)
        or {str(scope) for scope in scopes} != set(expected_scopes)
        or not isinstance(expires_at, str)
    ):
        raise ValueError("connector_secret_invalid")
    if not isinstance(refresh_token, str) or not refresh_token:
        # Nothing here can renew itself. Only a person re-consenting can.
        raise GoogleAuthorizationRevoked("authorization_required")
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError as error:
        raise ValueError("connector_secret_invalid") from error
    if expiry.tzinfo is None:
        raise ValueError("connector_secret_invalid")
    return StoredCredential(
        secret_id=secret_id,
        provider=str(row["provider"]),
        key_version=str(row["key_version"]),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expiry,
        scopes=[str(scope) for scope in scopes],
    )


async def store_renewed_credential(
    pool: asyncpg.Pool,
    sync: ClaimedSync,
    credential: StoredCredential,
    *,
    access_token: str,
    expires_at: datetime,
    encryption_key: bytes,
    key_version: str,
) -> StoredCredential:
    """Seal the renewed grant, retire the old row, and repoint the connector.

    Same envelope shape the API writes on first consent, so either side can read
    what the other stored. The refresh token is carried across unchanged:
    Google does not reissue one on a refresh, and losing it here would turn the
    next expiry back into a manual re-consent.
    """
    payload = {
        "access_token": access_token,
        "refresh_token": credential.refresh_token,
        "expires_at": expires_at.isoformat(),
        "scopes": credential.scopes,
    }
    aad = secret_aad(sync.tenant_id, sync.connector_id, credential.provider, key_version)
    nonce = secrets_module.token_bytes(12)
    ciphertext = AESGCM(encryption_key).encrypt(
        nonce, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), aad
    )
    async with pool.acquire() as connection, connection.transaction():
        await set_tenant(connection, sync.tenant_id)
        await connection.execute(
            """
            UPDATE connector_secret SET revoked_at=now()
            WHERE tenant_id=$1 AND connector_id=$2 AND revoked_at IS NULL
            """,
            sync.tenant_id,
            sync.connector_id,
        )
        secret_id = await connection.fetchval(
            """
            INSERT INTO connector_secret(
              tenant_id,connector_id,provider,ciphertext,nonce,aad_hash,key_version
            ) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id
            """,
            sync.tenant_id,
            sync.connector_id,
            credential.provider,
            ciphertext,
            nonce,
            hashlib.sha256(aad).hexdigest(),
            key_version,
        )
        await connection.execute(
            """
            UPDATE connector SET secret_ref=$3,token_expires_at=$4
            WHERE id=$1 AND tenant_id=$2
            """,
            sync.connector_id,
            sync.tenant_id,
            f"{SECRET_PREFIX}{secret_id}",
            expires_at,
        )
    return StoredCredential(
        secret_id=secret_id,
        provider=credential.provider,
        key_version=key_version,
        access_token=access_token,
        refresh_token=credential.refresh_token,
        expires_at=expires_at,
        scopes=credential.scopes,
    )


class AccessTokenManager:
    """Hands out a usable access token, renewing it as often as it takes."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        sync: ClaimedSync,
        *,
        encryption_key: bytes,
        key_version: str,
        refresher: TokenRefresher | None,
        expected_scopes: frozenset[str],
    ) -> None:
        self._pool = pool
        self._sync = sync
        self._encryption_key = encryption_key
        self._key_version = key_version
        self._refresher = refresher
        self._expected_scopes = expected_scopes
        self._credential: StoredCredential | None = None

    async def _current(self) -> StoredCredential:
        if self._credential is None:
            self._credential = await load_credential(
                self._pool,
                self._sync,
                self._encryption_key,
                expected_scopes=self._expected_scopes,
            )
        return self._credential

    async def token(self) -> str:
        credential = await self._current()
        if credential.is_fresh(datetime.now(UTC)):
            return credential.access_token
        return await self.renew()

    async def renew(self) -> str:
        credential = await self._current()
        if self._refresher is None:
            # Naming the real cause. Reporting `authorization_required` here
            # would send a tenant to a consent screen to fix a missing client
            # secret on the server, which cannot possibly work.
            raise ValueError("token_refresh_not_configured")
        refreshed = await self._refresher.refresh(credential.refresh_token)
        if refreshed.scopes and set(refreshed.scopes) != set(self._expected_scopes):
            # The grant is not the one that was consented to. Writing it back
            # would silently widen what this connector can reach.
            raise GoogleAuthorizationRevoked("authorization_required")
        self._credential = await store_renewed_credential(
            self._pool,
            self._sync,
            credential,
            access_token=refreshed.access_token,
            expires_at=refreshed.expires_at,
            encryption_key=self._encryption_key,
            key_version=self._key_version,
        )
        return self._credential.access_token


@dataclass(frozen=True, slots=True)
class Written:
    """What a batch of rows actually did to the table.

    `total` is every row written -- inserted or updated. `new` is the subset
    that did not exist before.

    Reported separately because collapsing them is actively misleading, and in
    exactly the direction that matters. A healthy incremental sync re-reads a
    window it has already stored: every row is an update, so a count of inserts
    alone reads `0` and says "this sync stored nothing" about a sync that
    stored everything it was given. That number is what an operator looks at to
    decide whether the data path works, and on 2026-09-08 it produced exactly
    that wrong conclusion about a GA4 sync that was working correctly.

    Keeping both also preserves the signal that was worth having: `new` still
    distinguishes a sync discovering data from one refreshing it.
    """

    total: int
    new: int

    def __add__(self, other: Written) -> Written:
        return Written(self.total + other.total, self.new + other.new)


ZERO_WRITTEN = Written(0, 0)


class SyncProgress:
    """The running totals of a sync, and the only writer of its checkpoint.

    Counts are cumulative across resumes: a claim reports what the row already
    recorded, and this adds what the current attempt has done on top. Writing
    them any other way would make a resumed sync report less work than it did.
    """

    def __init__(self, pool: asyncpg.Pool, sync: ClaimedSync) -> None:
        self._pool = pool
        self._sync = sync
        self.days_completed = sync.base_days_completed
        self.rows_seen = sync.base_rows_seen
        self.rows_upserted = sync.base_rows_upserted
        self.rows_new = sync.base_rows_new

    @property
    def counts(self) -> dict[str, int]:
        return {
            "days_completed": self.days_completed,
            "rows_seen": self.rows_seen,
            "rows_upserted": self.rows_upserted,
            "rows_new": self.rows_new,
        }

    async def write(
        self,
        cursor: Mapping[str, object],
        *,
        days_completed: int,
        rows_seen: int,
        rows_upserted: int,
        rows_new: int = 0,
    ) -> None:
        self.days_completed = self._sync.base_days_completed + days_completed
        self.rows_seen = self._sync.base_rows_seen + rows_seen
        self.rows_upserted = self._sync.base_rows_upserted + rows_upserted
        self.rows_new = self._sync.base_rows_new + rows_new
        async with self._pool.acquire() as connection, connection.transaction():
            await set_tenant(connection, self._sync.tenant_id)
            await connection.execute(
                f"""
                UPDATE connector_sync
                SET cursor_json=$3::jsonb,counts_json=$4::jsonb,
                    lease_until=now()+interval '{LEASE_INTERVAL}'
                WHERE id=$1 AND tenant_id=$2 AND status='running'
                """,
                self._sync.id,
                self._sync.tenant_id,
                json.dumps(dict(cursor), sort_keys=True, separators=(",", ":")),
                json.dumps(self.counts, sort_keys=True, separators=(",", ":")),
            )


async def complete_sync(
    pool: asyncpg.Pool, sync: ClaimedSync, counts: Mapping[str, int]
) -> None:
    payload_counts = dict(counts)
    async with pool.acquire() as connection, connection.transaction():
        await set_tenant(connection, sync.tenant_id)
        await connection.execute(
            """
            UPDATE connector_sync SET status='completed',finished_at=now(),lease_until=null,
              counts_json=$3::jsonb,error_code=null
            WHERE id=$1 AND tenant_id=$2 AND status='running'
            """,
            sync.id,
            sync.tenant_id,
            json.dumps(payload_counts),
        )
        await connection.execute(
            "UPDATE connector SET last_sync_at=now() WHERE id=$1 AND tenant_id=$2",
            sync.connector_id,
            sync.tenant_id,
        )
        await connection.execute(
            """
            INSERT INTO outbox_event(
              tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload
            ) VALUES($1,'connector.sync_completed',1,'connector_sync',$2,$3::jsonb)
            """,
            sync.tenant_id,
            sync.id,
            json.dumps(
                {
                    "sync_id": str(sync.id),
                    "connector_id": str(sync.connector_id),
                    "counts": payload_counts,
                }
            ),
        )


async def fail_sync(pool: asyncpg.Pool, sync: ClaimedSync, error_code: str) -> None:
    safe_code = error_code[:80] if error_code else "sync_failed"
    async with pool.acquire() as connection, connection.transaction():
        await set_tenant(connection, sync.tenant_id)
        await connection.execute(
            """
            UPDATE connector_sync SET status='failed',finished_at=now(),lease_until=null,error_code=$3
            WHERE id=$1 AND tenant_id=$2 AND status='running'
            """,
            sync.id,
            sync.tenant_id,
            safe_code,
        )
        if safe_code == "authorization_required":
            await connection.execute(
                """
                UPDATE connector SET status='reauthorization_required'
                WHERE id=$1 AND tenant_id=$2
                """,
                sync.connector_id,
                sync.tenant_id,
            )
        await connection.execute(
            """
            INSERT INTO outbox_event(
              tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload
            ) VALUES($1,'connector.sync_failed',1,'connector_sync',$2,$3::jsonb)
            """,
            sync.tenant_id,
            sync.id,
            json.dumps({"sync_id": str(sync.id), "error_code": safe_code}),
        )


def reclaimed_messages(reply: object) -> list[tuple[str, Mapping[str, str]]]:
    if not isinstance(reply, (list, tuple)) or len(reply) < 2 or not isinstance(reply[1], list):
        return []
    return [
        (message_id, fields)
        for message_id, fields in reply[1]
        if isinstance(message_id, str) and isinstance(fields, Mapping)
    ]


def new_messages(reply: object) -> list[tuple[str, Mapping[str, str]]]:
    if not isinstance(reply, list):
        return []
    result: list[tuple[str, Mapping[str, str]]] = []
    for stream in reply:
        if not isinstance(stream, (list, tuple)) or len(stream) != 2 or not isinstance(stream[1], list):
            continue
        result.extend(
            (message_id, fields)
            for message_id, fields in stream[1]
            if isinstance(message_id, str) and isinstance(fields, Mapping)
        )
    return result
