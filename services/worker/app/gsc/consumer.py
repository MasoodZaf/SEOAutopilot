from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from redis.exceptions import ResponseError

from app.gsc.client import READONLY_SCOPE, SearchConsoleClient, SearchConsoleError
from app.gsc.sync import MetricRecord, MetricSink, SyncCursor, sync_search_analytics

STREAM = "seo-autopilot:events"
GROUP = "gsc-sync"
logger = logging.getLogger(__name__)


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
    id: UUID
    tenant_id: UUID
    connector_id: UUID
    site_id: UUID
    property_ref: str
    secret_ref: str
    range_start: date
    range_end: date
    cursor: SyncCursor | None
    base_days_completed: int
    base_rows_seen: int
    base_rows_upserted: int


def decode_encryption_key(encoded: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("invalid_connector_secret_key") from error
    if len(key) != 32:
        raise ValueError("invalid_connector_secret_key")
    return key


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


def parse_cursor(value: object) -> SyncCursor | None:
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
    raw_start_row = value.get("start_row", 0)
    if not isinstance(raw_day, str) or not isinstance(raw_start_row, int):
        raise TypeError("invalid_sync_cursor")
    try:
        return SyncCursor(date.fromisoformat(raw_day), raw_start_row)
    except ValueError as error:
        raise ValueError("invalid_sync_cursor") from error


async def _set_tenant(connection: Any, tenant_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))


async def claim_sync(
    pool: asyncpg.Pool, tenant_id: UUID, sync_id: UUID
) -> ClaimedSync | None:
    async with pool.acquire() as connection, connection.transaction():
        await _set_tenant(connection, tenant_id)
        row = await connection.fetchrow(
            """
            UPDATE connector_sync cs
            SET status='running', started_at=coalesce(started_at,now()),
                lease_until=now()+interval '5 minutes', error_code=null
            FROM connector c, site s
            WHERE cs.id=$1 AND cs.tenant_id=$2
              AND c.id=cs.connector_id AND c.tenant_id=cs.tenant_id
              AND s.id=c.site_id AND s.tenant_id=c.tenant_id
              AND c.status='active' AND c.type='google_search_console'
              AND c.secret_ref IS NOT NULL
              AND (cs.status='queued' OR (cs.status='running' AND cs.lease_until<now()))
            RETURNING cs.id,cs.tenant_id,cs.connector_id,c.site_id,
              c.external_account_ref,c.secret_ref,cs.range_start,cs.range_end,
              cs.cursor_json,cs.counts_json
            """,
            sync_id,
            tenant_id,
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
        cursor=parse_cursor(row["cursor_json"]),
        base_days_completed=int(counts.get("days_completed", 0)),
        base_rows_seen=int(counts.get("rows_seen", 0)),
        base_rows_upserted=int(counts.get("rows_upserted", 0)),
    )


async def load_access_token(
    pool: asyncpg.Pool, sync: ClaimedSync, encryption_key: bytes
) -> str:
    prefix = "db-envelope://"
    if not sync.secret_ref.startswith(prefix):
        raise ValueError("unsupported_secret_ref")
    try:
        secret_id = UUID(sync.secret_ref.removeprefix(prefix))
    except ValueError as error:
        raise ValueError("invalid_secret_ref") from error
    async with pool.acquire() as connection, connection.transaction():
        await _set_tenant(connection, sync.tenant_id)
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
    scopes = payload.get("scopes")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(scopes, list)
        or scopes != [READONLY_SCOPE]
        or not isinstance(expires_at, str)
    ):
        raise ValueError("connector_secret_invalid")
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError as error:
        raise ValueError("connector_secret_invalid") from error
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise ValueError("authorization_required")
    return access_token


class PostgresMetricSink(MetricSink):
    def __init__(self, pool: asyncpg.Pool, sync: ClaimedSync) -> None:
        self.pool = pool
        self.sync = sync
        self.days_completed = sync.base_days_completed
        self.rows_seen = sync.base_rows_seen
        self.rows_upserted = sync.base_rows_upserted

    async def upsert_metrics(self, records: list[MetricRecord]) -> int:
        if not records:
            return 0
        inserted = 0
        async with self.pool.acquire() as connection, connection.transaction():
            await _set_tenant(connection, self.sync.tenant_id)
            for record in records:
                result = await connection.fetchval(
                    """
                    INSERT INTO search_metric(
                      tenant_id,site_id,page_id,metric_date,query_hash,page_url,page_url_hash,
                      country,device,search_type,clicks,impressions,ctr,position,source_sync_id
                    ) VALUES(
                      $1,$2,(SELECT id FROM page WHERE tenant_id=$1 AND site_id=$2
                        AND normalized_url=$5 LIMIT 1),$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14
                    )
                    ON CONFLICT(
                      tenant_id,site_id,metric_date,query_hash,page_url_hash,country,device,search_type
                    ) DO UPDATE SET clicks=excluded.clicks,impressions=excluded.impressions,
                      ctr=excluded.ctr,position=excluded.position,source_sync_id=excluded.source_sync_id,
                      ingested_at=now()
                    RETURNING (xmax=0)
                    """,
                    record.tenant_id,
                    record.site_id,
                    record.metric_date,
                    record.query_hash,
                    record.page_url,
                    record.page_url_hash,
                    record.country,
                    record.device,
                    record.search_type,
                    record.clicks,
                    record.impressions,
                    record.ctr,
                    record.position,
                    record.source_sync_id,
                )
                inserted += int(bool(result))
        return inserted

    async def save_checkpoint(
        self,
        cursor: SyncCursor,
        *,
        days_completed: int,
        rows_seen: int,
        rows_upserted: int,
    ) -> None:
        self.days_completed = self.sync.base_days_completed + days_completed
        self.rows_seen = self.sync.base_rows_seen + rows_seen
        self.rows_upserted = self.sync.base_rows_upserted + rows_upserted
        async with self.pool.acquire() as connection, connection.transaction():
            await _set_tenant(connection, self.sync.tenant_id)
            await connection.execute(
                """
                UPDATE connector_sync
                SET cursor_json=$3::jsonb,counts_json=$4::jsonb,
                    lease_until=now()+interval '5 minutes'
                WHERE id=$1 AND tenant_id=$2 AND status='running'
                """,
                self.sync.id,
                self.sync.tenant_id,
                json.dumps({"day": cursor.day.isoformat(), "start_row": cursor.start_row}),
                json.dumps(
                    {
                        "days_completed": self.days_completed,
                        "rows_seen": self.rows_seen,
                        "rows_upserted": self.rows_upserted,
                    }
                ),
            )


async def complete_sync(pool: asyncpg.Pool, sync: ClaimedSync, sink: PostgresMetricSink) -> None:
    counts = {
        "days_completed": sink.days_completed,
        "rows_seen": sink.rows_seen,
        "rows_upserted": sink.rows_upserted,
    }
    async with pool.acquire() as connection, connection.transaction():
        await _set_tenant(connection, sync.tenant_id)
        await connection.execute(
            """
            UPDATE connector_sync SET status='completed',finished_at=now(),lease_until=null,
              counts_json=$3::jsonb,error_code=null
            WHERE id=$1 AND tenant_id=$2 AND status='running'
            """,
            sync.id,
            sync.tenant_id,
            json.dumps(counts),
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
            json.dumps({"sync_id": str(sync.id), "connector_id": str(sync.connector_id), "counts": counts}),
        )


async def fail_sync(pool: asyncpg.Pool, sync: ClaimedSync, error_code: str) -> None:
    safe_code = error_code[:80] if error_code else "sync_failed"
    async with pool.acquire() as connection, connection.transaction():
        await _set_tenant(connection, sync.tenant_id)
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


async def run_gsc_consumer(
    pool: asyncpg.Pool,
    streams: SyncStream,
    consumer: str,
    *,
    encryption_key: bytes,
    query_hash_key: bytes,
) -> None:
    try:
        await streams.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise
    while True:
        reclaimed = reclaimed_messages(
            await streams.xautoclaim(
                STREAM,
                GROUP,
                consumer,
                min_idle_time=60_000,
                start_id="0-0",
                count=1,
            )
        )
        messages = reclaimed or new_messages(
            await streams.xreadgroup(
                GROUP,
                consumer,
                {STREAM: ">"},
                count=1,
                block=5_000,
            )
        )
        for message_id, fields in messages:
            if fields.get("type") != "connector.sync_requested":
                await streams.xack(STREAM, GROUP, message_id)
                continue
            try:
                tenant_id = UUID(fields["tenant_id"])
                sync_id = UUID(fields["aggregate_id"])
                sync = await claim_sync(pool, tenant_id, sync_id)
                if sync is None:
                    await streams.xack(STREAM, GROUP, message_id)
                    continue
                try:
                    access_token = await load_access_token(pool, sync, encryption_key)
                    sink = PostgresMetricSink(pool, sync)
                    provider = SearchConsoleClient()
                    try:
                        await sync_search_analytics(
                            provider,
                            sink,
                            tenant_id=sync.tenant_id,
                            site_id=sync.site_id,
                            sync_id=sync.id,
                            property_ref=sync.property_ref,
                            access_token=access_token,
                            range_start=sync.range_start,
                            range_end=sync.range_end,
                            cursor=sync.cursor,
                            query_hash_key=query_hash_key,
                        )
                    finally:
                        await provider.close()
                    await complete_sync(pool, sync, sink)
                except (SearchConsoleError, TypeError, ValueError, RuntimeError) as error:
                    await fail_sync(pool, sync, str(error))
                await streams.xack(STREAM, GROUP, message_id)
            except (KeyError, TypeError, ValueError):
                logger.exception("invalid GSC sync event", extra={"event_id": message_id})
                await streams.xack(STREAM, GROUP, message_id)
            except Exception:
                logger.exception("GSC sync failed", extra={"event_id": message_id})


def require_secret_bytes(value: str | None, error_code: str) -> bytes:
    if value is None:
        raise ValueError(error_code)
    encoded = value.encode()
    if len(encoded) < 32:
        raise ValueError(error_code)
    return encoded
