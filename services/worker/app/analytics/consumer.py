"""The GA4 half of a connector sync.

Everything a connector sync shares -- the lease, the envelope, token renewal,
checkpointing, closing the row out -- comes from `app.connectors.runtime`. What
is here is what only GA4 does: page a day's landing-page report and write
`analytics_metric`.

Both consumers read the same stream and are offered the same
`connector.sync_requested` message. Each claims only its own connector type, so
a Search Console row is not something this has to remember to reject -- it
simply does not match the claim.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

import asyncpg
from redis.exceptions import ResponseError

from app.analytics.client import (
    ANALYTICS_READONLY_SCOPE,
    AnalyticsDataClient,
    AnalyticsDataError,
    LandingPagePage,
    landing_page_path,
)
from app.analytics.sync import (
    AnalyticsCursor,
    AnalyticsRecord,
    AnalyticsSink,
    sync_landing_pages,
)
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

GROUP = "analytics-sync"
CONNECTOR_TYPE = "google_analytics"
REQUIRED_SCOPES = frozenset({ANALYTICS_READONLY_SCOPE})

logger = logging.getLogger(__name__)


def parse_cursor(value: object) -> AnalyticsCursor | None:
    parsed = parse_day_offset_cursor(value, offset_field="offset")
    return None if parsed is None else AnalyticsCursor(*parsed)


class CredentialedAnalytics:
    """A GA4 source that carries, and can replace, its own token."""

    def __init__(self, client: AnalyticsDataClient, tokens: AccessTokenManager) -> None:
        self._client = client
        self._tokens = tokens

    async def query_day(self, property_ref: str, day: date, offset: int) -> LandingPagePage:
        token = await self._tokens.token()
        try:
            return await self._client.query_day(property_ref, token, day, offset)
        except AnalyticsDataError as error:
            if str(error) != "authorization_required":
                raise
        # Renewed once, and only once: a second refusal with a token minted
        # seconds earlier is the grant being gone, not a clock problem.
        return await self._client.query_day(
            property_ref, await self._tokens.renew(), day, offset
        )


class PostgresAnalyticsSink(AnalyticsSink):
    """Writes the report, resolving a crawled page where the path names one.

    The landing page is stored as GA4 reported it, query string included, so two
    reported rows are never merged. `page_id` is resolved from the normalised
    path against the site's own origin, which lets several reported rows point
    at one crawled page without any of them losing their sessions.
    """

    def __init__(self, pool: asyncpg.Pool, sync: ClaimedSync) -> None:
        self.pool = pool
        self.sync = sync
        self.progress = SyncProgress(pool, sync)

    async def upsert_metrics(self, records: list[AnalyticsRecord]) -> int:
        if not records:
            return 0
        inserted = 0
        async with self.pool.acquire() as connection, connection.transaction():
            await set_tenant(connection, self.sync.tenant_id)
            origin = await connection.fetchval(
                "SELECT canonical_origin FROM site WHERE id=$1 AND tenant_id=$2",
                self.sync.site_id,
                self.sync.tenant_id,
            )
            for record in records:
                normalized = _normalized_url(origin, record.landing_page)
                result = await connection.fetchval(
                    """
                    INSERT INTO analytics_metric(
                      tenant_id,site_id,page_id,metric_date,landing_page,landing_page_hash,
                      channel_group,device,sessions,engaged_sessions,users,views,
                      engagement_duration_seconds,key_events,source_sync_id
                    ) VALUES(
                      $1,$2,(SELECT id FROM page WHERE tenant_id=$1 AND site_id=$2
                        AND normalized_url=$15 LIMIT 1),$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14
                    )
                    ON CONFLICT(
                      tenant_id,site_id,metric_date,landing_page_hash,channel_group,device
                    ) DO UPDATE SET sessions=excluded.sessions,
                      engaged_sessions=excluded.engaged_sessions,users=excluded.users,
                      views=excluded.views,
                      engagement_duration_seconds=excluded.engagement_duration_seconds,
                      key_events=excluded.key_events,page_id=excluded.page_id,
                      source_sync_id=excluded.source_sync_id,ingested_at=now()
                    RETURNING (xmax=0)
                    """,
                    record.tenant_id,
                    record.site_id,
                    record.metric_date,
                    record.landing_page,
                    record.landing_page_hash,
                    record.channel_group,
                    record.device,
                    record.sessions,
                    record.engaged_sessions,
                    record.users,
                    record.views,
                    record.engagement_duration_seconds,
                    record.key_events,
                    record.source_sync_id,
                    normalized,
                )
                inserted += int(bool(result))
        return inserted

    async def save_checkpoint(
        self,
        cursor: AnalyticsCursor,
        *,
        days_completed: int,
        rows_seen: int,
        rows_upserted: int,
    ) -> None:
        await self.progress.write(
            {"day": cursor.day.isoformat(), "offset": cursor.offset},
            days_completed=days_completed,
            rows_seen=rows_seen,
            rows_upserted=rows_upserted,
        )


def _normalized_url(origin: object, landing_page: str) -> str:
    """The crawler's normalised form of a reported landing page.

    Must match what the crawler stored or the join silently finds nothing: the
    origin with no trailing slash, the path with no query and no trailing slash
    except at the root. A reported value that is not a path -- GA4's `(other)`
    bucket -- resolves to no page rather than to a fabricated URL.
    """
    path = landing_page_path(landing_page)
    if path is None or not isinstance(origin, str) or not origin:
        return ""
    return f"{origin.rstrip('/')}{path}"


async def run_analytics_consumer(
    pool: asyncpg.Pool,
    streams: SyncStream,
    consumer: str,
    *,
    encryption_key: bytes,
    key_version: str = "local-v1",
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
                STREAM, GROUP, consumer, min_idle_time=60_000, start_id="0-0", count=1
            )
        )
        messages = reclaimed or new_messages(
            await streams.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=5_000)
        )
        for message_id, fields in messages:
            if fields.get("type") != "connector.sync_requested":
                await streams.xack(STREAM, GROUP, message_id)
                continue
            try:
                tenant_id = UUID(fields["tenant_id"])
                sync_id = UUID(fields["aggregate_id"])
                # A Search Console sync row is offered here too, and simply
                # does not match.
                sync = await claim_sync(
                    pool, tenant_id, sync_id, connector_type=CONNECTOR_TYPE
                )
                if sync is None:
                    await streams.xack(STREAM, GROUP, message_id)
                    continue
                try:
                    sink = PostgresAnalyticsSink(pool, sync)
                    tokens = AccessTokenManager(
                        pool,
                        sync,
                        encryption_key=encryption_key,
                        key_version=key_version,
                        refresher=refresher,
                        expected_scopes=REQUIRED_SCOPES,
                    )
                    client = AnalyticsDataClient()
                    try:
                        await sync_landing_pages(
                            CredentialedAnalytics(client, tokens),
                            sink,
                            tenant_id=sync.tenant_id,
                            site_id=sync.site_id,
                            sync_id=sync.id,
                            property_ref=sync.property_ref,
                            range_start=sync.range_start,
                            range_end=sync.range_end,
                            cursor=parse_cursor(sync.raw_cursor),
                        )
                    finally:
                        await client.close()
                    await complete_sync(pool, sync, sink.progress.counts)
                except (AnalyticsDataError, TypeError, ValueError, RuntimeError) as error:
                    await fail_sync(pool, sync, str(error))
                await streams.xack(STREAM, GROUP, message_id)
            except (KeyError, TypeError, ValueError):
                logger.exception("invalid analytics sync event", extra={"event_id": message_id})
                await streams.xack(STREAM, GROUP, message_id)
            except Exception:
                logger.exception("analytics sync failed", extra={"event_id": message_id})
