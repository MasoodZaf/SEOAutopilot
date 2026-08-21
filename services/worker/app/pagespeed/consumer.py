import logging
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from redis.exceptions import ResponseError

from app.pagespeed.client import PageSpeedError, PageSpeedProvider

STREAM = "seo-autopilot:events"
GROUP = "pagespeed"
logger = logging.getLogger(__name__)


class Pool(Protocol):
    def acquire(self) -> Any: ...


class Stream(Protocol):
    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> bool: ...
    async def xautoclaim(self, name: str, groupname: str, consumername: str, **kwargs: Any) -> Any: ...
    async def xreadgroup(self, groupname: str, consumername: str, streams: dict[str, str], **kwargs: Any) -> Any: ...
    async def xack(self, name: str, groupname: str, *ids: str) -> int: ...


def _messages(reply: object, reclaimed: bool = False) -> list[tuple[str, Mapping[str, str]]]:
    source = reply[1] if reclaimed and isinstance(reply, (list, tuple)) and len(reply) >= 2 else reply
    if not isinstance(source, list):
        return []
    if reclaimed:
        return [(mid, fields) for mid, fields in source if isinstance(mid, str) and isinstance(fields, Mapping)]
    result: list[tuple[str, Mapping[str, str]]] = []
    for stream in source:
        if isinstance(stream, (list, tuple)) and len(stream) == 2 and isinstance(stream[1], list):
            result.extend((mid, fields) for mid, fields in stream[1] if isinstance(mid, str) and isinstance(fields, Mapping))
    return result


async def process_run(connection: Any, tenant_id: UUID, run_id: UUID, provider: PageSpeedProvider) -> None:
    row = await connection.fetchrow(
        """
        UPDATE performance_run pr SET status='running',started_at=COALESCE(started_at,now()),
          attempts=attempts+1,lease_until=now()+interval '2 minutes',error_code=NULL
        FROM site s
        WHERE pr.id=$1 AND pr.tenant_id=$2 AND s.id=pr.site_id AND s.tenant_id=pr.tenant_id
          AND s.status='active' AND s.verified_at IS NOT NULL AND pr.attempts<3
          AND (pr.status='queued' OR (pr.status='running' AND pr.lease_until<now()))
        RETURNING pr.site_id,pr.page_id,pr.target_url,pr.strategy,s.normalized_host
        """,
        run_id,
        tenant_id,
    )
    if row is None:
        return
    parsed = urlsplit(row["target_url"])
    if parsed.hostname != row["normalized_host"] or parsed.scheme not in {"http", "https"}:
        await connection.execute("UPDATE performance_run SET status='failed',finished_at=now(),lease_until=NULL,error_code='target_scope_invalid' WHERE id=$1 AND tenant_id=$2", run_id, tenant_id)
        return
    try:
        result = await provider.analyze(row["target_url"], row["strategy"])
    except (PageSpeedError, ValueError) as error:
        code = str(error) if str(error) in {"provider_rate_limited", "provider_unavailable", "provider_request_rejected", "provider_response_too_large", "provider_response_invalid", "invalid_pagespeed_target", "invalid_pagespeed_strategy"} else "provider_failure"
        await connection.execute("UPDATE performance_run SET status='failed',finished_at=now(),lease_until=NULL,error_code=$3 WHERE id=$1 AND tenant_id=$2", run_id, tenant_id, code)
        return
    async with connection.transaction():
        await connection.execute(
            """
            INSERT INTO performance_observation(tenant_id,performance_run_id,site_id,page_id,strategy,source,lighthouse_version,performance_score,lcp_ms,inp_ms,cls,ttfb_ms)
            VALUES($1,$2,$3,$4,$5,'pagespeed_insights',$6,$7,$8,$9,$10,$11)
            ON CONFLICT(performance_run_id) DO NOTHING
            """,
            tenant_id, run_id, row["site_id"], row["page_id"], row["strategy"], result.lighthouse_version,
            result.performance_score, result.lcp_ms, result.inp_ms, result.cls, result.ttfb_ms,
        )
        await connection.execute("UPDATE performance_run SET status='completed',finished_at=now(),lease_until=NULL,error_code=NULL WHERE id=$1 AND tenant_id=$2", run_id, tenant_id)


async def run_pagespeed_consumer(pool: Pool, streams: Stream, consumer: str, provider: PageSpeedProvider) -> None:
    try:
        await streams.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise
    while True:
        reclaimed = _messages(await streams.xautoclaim(STREAM, GROUP, consumer, min_idle_time=60_000, start_id="0-0", count=1), True)
        messages = reclaimed or _messages(await streams.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=5_000))
        for message_id, fields in messages:
            if fields.get("type") != "performance.requested":
                await streams.xack(STREAM, GROUP, message_id)
                continue
            try:
                async with pool.acquire() as connection:
                    await process_run(connection, UUID(fields["tenant_id"]), UUID(fields["aggregate_id"]), provider)
                await streams.xack(STREAM, GROUP, message_id)
            except (KeyError, ValueError):
                logger.exception("invalid performance event", extra={"event_id": message_id})
                await streams.xack(STREAM, GROUP, message_id)
            except Exception:
                logger.exception("performance ingestion failed", extra={"event_id": message_id})
