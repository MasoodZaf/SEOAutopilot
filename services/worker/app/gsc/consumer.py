"""The Search Console half of a connector sync.

Claiming the row, reading the grant, renewing a token and closing the sync out
are the same for every connector and live in `app.connectors.runtime`. What is
left here is what only Search Console does: page a day of search analytics, seal
each query term, and write `search_metric`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg
from redis.exceptions import ResponseError

from app.connectors.google_oauth import TokenRefresher
from app.connectors.runtime import (
    STREAM,
    AccessTokenManager,
    ClaimedSync,
    SyncProgress,
    SyncStream,
    claim_sync,
    complete_sync,
    fail_sync,
    new_messages,
    parse_day_offset_cursor,
    reclaimed_messages,
    set_tenant,
)
from app.gsc.client import (
    READONLY_SCOPE,
    SearchAnalyticsRow,
    SearchConsoleClient,
    SearchConsoleError,
)
from app.gsc.sync import MetricRecord, MetricSink, SyncCursor, sync_search_analytics
from app.keywords.cluster import is_question, tokenize
from app.keywords.secrets import MAX_TERM_LENGTH, seal_query

GROUP = "gsc-sync"
CONNECTOR_TYPE = "google_search_console"
REQUIRED_SCOPES = frozenset({READONLY_SCOPE})

logger = logging.getLogger(__name__)


def parse_cursor(value: object) -> SyncCursor | None:
    parsed = parse_day_offset_cursor(value, offset_field="start_row")
    return None if parsed is None else SyncCursor(*parsed)


class CredentialedSearchConsole:
    """A Search Console source that carries, and can replace, its own token.

    A 490-day backfill makes roughly a thousand requests. An access token lives
    an hour. Without the retry below, a backfill that runs long enough dies
    partway with `authorization_required` and marks a perfectly good connector
    as needing re-consent.
    """

    def __init__(self, client: SearchConsoleClient, tokens: AccessTokenManager) -> None:
        self._client = client
        self._tokens = tokens

    async def query_day(
        self, property_ref: str, day: date, start_row: int
    ) -> list[SearchAnalyticsRow]:
        token = await self._tokens.token()
        try:
            return await self._client.query_day(property_ref, token, day, start_row)
        except SearchConsoleError as error:
            if str(error) != "authorization_required":
                raise
        # Renewed once, and only once: a second refusal with a token minted
        # seconds earlier is the grant being gone, not a clock problem.
        return await self._client.query_day(
            property_ref, await self._tokens.renew(), day, start_row
        )


class PostgresMetricSink(MetricSink):
    def __init__(
        self,
        pool: asyncpg.Pool,
        sync: ClaimedSync,
        *,
        query_encryption_key: bytes,
        query_key_version: str,
    ) -> None:
        self.pool = pool
        self.sync = sync
        self.query_encryption_key = query_encryption_key
        self.query_key_version = query_key_version
        self.progress = SyncProgress(pool, sync)

    async def _upsert_query_term(self, connection: Any, record: MetricRecord) -> None:
        """Store the readable term once per (site, query) inside an envelope.

        search_metric keeps only the HMAC. Re-sealing on every sighting would
        churn the ciphertext for no benefit, so an existing row only has its
        last_seen_at advanced.
        """
        term = record.query_text.strip()[:MAX_TERM_LENGTH]
        if not term:
            return
        ciphertext, nonce, aad_hash = seal_query(
            self.query_encryption_key,
            record.tenant_id,
            record.site_id,
            self.query_key_version,
            term,
        )
        await connection.execute(
            """
            INSERT INTO search_query(
              tenant_id,site_id,query_hash,ciphertext,nonce,aad_hash,key_version,
              term_length,token_count,is_question
            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT(tenant_id,site_id,query_hash) DO UPDATE SET last_seen_at=now()
            """,
            record.tenant_id,
            record.site_id,
            record.query_hash,
            ciphertext,
            nonce,
            aad_hash,
            self.query_key_version,
            len(term),
            min(len(tokenize(term)) or 1, 60),
            is_question(term),
        )

    async def upsert_metrics(self, records: list[MetricRecord]) -> int:
        if not records:
            return 0
        inserted = 0
        async with self.pool.acquire() as connection, connection.transaction():
            await set_tenant(connection, self.sync.tenant_id)
            for record in records:
                await self._upsert_query_term(connection, record)
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
        await self.progress.write(
            {"day": cursor.day.isoformat(), "start_row": cursor.start_row},
            days_completed=days_completed,
            rows_seen=rows_seen,
            rows_upserted=rows_upserted,
        )


async def run_gsc_consumer(
    pool: asyncpg.Pool,
    streams: SyncStream,
    consumer: str,
    *,
    encryption_key: bytes,
    query_hash_key: bytes,
    query_key_version: str = "local-v1",
    refresher: TokenRefresher | None = None,
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
                # A GA4 sync row is offered here too and simply does not match.
                sync = await claim_sync(
                    pool, tenant_id, sync_id, connector_type=CONNECTOR_TYPE
                )
                if sync is None:
                    await streams.xack(STREAM, GROUP, message_id)
                    continue
                try:
                    sink = PostgresMetricSink(
                        pool,
                        sync,
                        query_encryption_key=encryption_key,
                        query_key_version=query_key_version,
                    )
                    tokens = AccessTokenManager(
                        pool,
                        sync,
                        encryption_key=encryption_key,
                        key_version=query_key_version,
                        refresher=refresher,
                        expected_scopes=REQUIRED_SCOPES,
                    )
                    client = SearchConsoleClient()
                    try:
                        await sync_search_analytics(
                            CredentialedSearchConsole(client, tokens),
                            sink,
                            tenant_id=sync.tenant_id,
                            site_id=sync.site_id,
                            sync_id=sync.id,
                            property_ref=sync.property_ref,
                            range_start=sync.range_start,
                            range_end=sync.range_end,
                            cursor=parse_cursor(sync.raw_cursor),
                            query_hash_key=query_hash_key,
                        )
                    finally:
                        await client.close()
                    await complete_sync(pool, sync, sink.progress.counts)
                except (SearchConsoleError, TypeError, ValueError, RuntimeError) as error:
                    await fail_sync(pool, sync, str(error))
                await streams.xack(STREAM, GROUP, message_id)
            except (KeyError, TypeError, ValueError):
                logger.exception("invalid GSC sync event", extra={"event_id": message_id})
                await streams.xack(STREAM, GROUP, message_id)
            except Exception:
                logger.exception("GSC sync failed", extra={"event_id": message_id})
