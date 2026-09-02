"""Executes queued routine runs.

A routine run only gathers evidence and produces reports. It never deploys and
never approves: those paths stay behind the governed proposal lifecycle and its
fail-closed checks. Kinds that are not implemented yet record `skipped` with a
reason rather than reporting a success they did not achieve.
"""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from redis.exceptions import ResponseError

from app.keywords.analysis import run_keyword_analysis
from app.routines.reports import build_weekly_digest, content_hash
from app.routines.sitemap_coverage import build_sitemap_coverage

STREAM = "seo-autopilot:events"
GROUP = "routines"
LEASE_MINUTES = 10
DIGEST_PERIOD_DAYS = 7
DEFAULT_CRAWL_MAX_PAGES = 500
DEFAULT_CRAWL_MAX_DEPTH = 5
logger = logging.getLogger(__name__)

# Kinds landing in later phases. Recorded honestly instead of faked.
UNIMPLEMENTED_KINDS = {"competitor_scan", "ai_visibility_scan"}

# Keyword clustering reads a rolling window of search evidence.
KEYWORD_WINDOW_DAYS = 28


class Pool(Protocol):
    def acquire(self) -> Any: ...


class Stream(Protocol):
    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> bool: ...
    async def xautoclaim(
        self, name: str, groupname: str, consumername: str, **kwargs: Any
    ) -> Any: ...
    async def xreadgroup(
        self, groupname: str, consumername: str, streams: dict[str, str], **kwargs: Any
    ) -> Any: ...
    async def xack(self, name: str, groupname: str, *ids: str) -> int: ...


def _messages(reply: object, reclaimed: bool = False) -> list[tuple[str, Mapping[str, str]]]:
    source = (
        reply[1]
        if reclaimed and isinstance(reply, (list, tuple)) and len(reply) >= 2
        else reply
    )
    if not isinstance(source, list):
        return []
    if reclaimed:
        return [
            (mid, fields)
            for mid, fields in source
            if isinstance(mid, str) and isinstance(fields, Mapping)
        ]
    result: list[tuple[str, Mapping[str, str]]] = []
    for stream in source:
        if isinstance(stream, (list, tuple)) and len(stream) == 2 and isinstance(stream[1], list):
            result.extend(
                (mid, fields)
                for mid, fields in stream[1]
                if isinstance(mid, str) and isinstance(fields, Mapping)
            )
    return result


CLAIM_RUN_SQL = """
UPDATE routine_run rr
SET status='running', started_at=COALESCE(rr.started_at,now()),
    attempts=rr.attempts+1, lease_until=now()+($3 * interval '1 minute'),
    error_code=NULL
FROM site s
WHERE rr.id=$1 AND rr.tenant_id=$2 AND s.id=rr.site_id AND s.tenant_id=rr.tenant_id
  AND s.status='active' AND s.verified_at IS NOT NULL AND NOT s.emergency_freeze
  AND rr.attempts<3
  AND (rr.status='queued' OR (rr.status='running' AND rr.lease_until<now()))
RETURNING rr.routine_id, rr.site_id, rr.kind, rr.scheduled_for
"""


async def _finish(
    connection: Any,
    run_id: UUID,
    tenant_id: UUID,
    routine_id: UUID,
    status: str,
    *,
    summary: dict[str, Any] | None = None,
    error_code: str | None = None,
    skip_reason: str | None = None,
) -> None:
    await connection.execute(
        """
        UPDATE routine_run
        SET status=$3, finished_at=now(), lease_until=NULL,
            summary_json=$4::jsonb, error_code=$5, skip_reason=$6
        WHERE id=$1 AND tenant_id=$2
        """,
        run_id, tenant_id, status,
        json.dumps(summary or {}, sort_keys=True, separators=(",", ":"), default=str),
        error_code, skip_reason,
    )
    await connection.execute(
        """
        UPDATE routine
        SET last_status=$3, last_run_at=now(), updated_at=now(),
            consecutive_failures=CASE WHEN $3='failed' THEN consecutive_failures+1 ELSE 0 END
        WHERE id=$1 AND tenant_id=$2
        """,
        routine_id, tenant_id, status,
    )


async def _run_site_audit(
    connection: Any, tenant_id: UUID, site_id: UUID, routine_id: UUID
) -> tuple[str, dict[str, Any], str | None]:
    """Queue a crawl unless one is already in flight for the site.

    The site's one-active-crawl rule is authoritative: a routine waits for the
    next slot rather than competing with an operator's manual crawl.
    """
    active = await connection.fetchval(
        """
        SELECT id FROM crawl_job
        WHERE tenant_id=$1 AND site_id=$2 AND status IN('queued','running')
        LIMIT 1
        """,
        tenant_id, site_id,
    )
    if active is not None:
        return "skipped", {"active_crawl_id": str(active)}, "crawl_already_active"

    origin = await connection.fetchval(
        "SELECT canonical_origin FROM site WHERE id=$1 AND tenant_id=$2", site_id, tenant_id
    )
    # A scheduled crawl is attributed to whoever created the routine, so the
    # audit trail still names a human actor.
    routine = await connection.fetchrow(
        "SELECT created_by,config_json FROM routine WHERE id=$1 AND tenant_id=$2",
        routine_id, tenant_id,
    )
    config = routine["config_json"] if routine else {}
    if isinstance(config, str):
        config = json.loads(config)
    crawl_id = await connection.fetchval(
        """
        INSERT INTO crawl_job(tenant_id,site_id,kind,status,requested_by,config_snapshot)
        VALUES($1,$2,'full','queued',$3,$4::jsonb)
        RETURNING id
        """,
        tenant_id, site_id, routine["created_by"],
        json.dumps(
            {
                "max_pages": min(int(config.get("max_pages", DEFAULT_CRAWL_MAX_PAGES)), 10_000),
                "max_depth": min(int(config.get("max_depth", DEFAULT_CRAWL_MAX_DEPTH)), 50),
                "render_policy": "adaptive",
                "canonical_origin": origin,
            },
            sort_keys=True, separators=(",", ":"),
        ),
    )
    await connection.execute(
        """
        INSERT INTO outbox_event(
          tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload)
        VALUES($1,'crawl.requested',1,'crawl_job',$2,$3::jsonb)
        """,
        tenant_id, crawl_id,
        json.dumps({"crawl_id": str(crawl_id), "site_id": str(site_id)},
                   sort_keys=True, separators=(",", ":")),
    )
    return "completed", {"crawl_id": str(crawl_id)}, None


async def _run_keyword_refresh(
    connection: Any,
    tenant_id: UUID,
    site_id: UUID,
    run_id: UUID,
    today: date,
    encryption_key: bytes | None,
) -> tuple[str, dict[str, Any], str | None]:
    """Recluster the site's search demand over a rolling window."""
    if encryption_key is None:
        return "skipped", {}, "query_encryption_key_unavailable"
    window_end = today - timedelta(days=1)
    window_start = window_end - timedelta(days=KEYWORD_WINDOW_DAYS - 1)
    available = await connection.fetchval(
        "SELECT count(*) FROM search_query WHERE tenant_id=$1 AND site_id=$2",
        tenant_id, site_id,
    )
    if not available:
        return "skipped", {}, "no_search_query_evidence"
    summary = await run_keyword_analysis(
        connection, tenant_id, site_id, window_start, window_end, encryption_key, run_id
    )
    if summary["queries_considered"] == 0:
        return "skipped", summary, "no_search_evidence_in_window"
    return "completed", summary, None


async def _run_sitemap_coverage(
    connection: Any, tenant_id: UUID, site_id: UUID, run_id: UUID
) -> tuple[str, dict[str, Any], str | None]:
    """Publish coverage for the most recent finished crawl.

    Coverage is only meaningful against the crawl that produced it, so a site
    with no completed crawl is skipped rather than reported as fully covered.
    """
    crawl = await connection.fetchrow(
        """
        SELECT id, finished_at FROM crawl_job
        WHERE tenant_id=$1 AND site_id=$2 AND status IN('completed','partial')
          AND finished_at IS NOT NULL
        ORDER BY finished_at DESC, id DESC LIMIT 1
        """,
        tenant_id, site_id,
    )
    if crawl is None:
        return "skipped", {}, "no_completed_crawl"
    declared = await connection.fetchval(
        "SELECT count(*) FROM sitemap_source WHERE tenant_id=$1 AND crawl_job_id=$2",
        tenant_id, crawl["id"],
    )
    if not declared:
        # Crawls predating sitemap inventory carry no sources to compare.
        return "skipped", {"crawl_job_id": str(crawl["id"])}, "no_sitemap_inventory"

    payload = await build_sitemap_coverage(connection, tenant_id, site_id, crawl["id"])
    digest = content_hash(payload)
    snapshot_day = crawl["finished_at"].date()
    report_id = await connection.fetchval(
        """
        INSERT INTO report(
          tenant_id,site_id,routine_run_id,kind,period_start,period_end,
          content_hash,payload_json)
        VALUES($1,$2,$3,'sitemap_coverage',$4,$4,$5,$6::jsonb)
        ON CONFLICT(tenant_id,site_id,kind,period_start,period_end) DO UPDATE
          SET routine_run_id=EXCLUDED.routine_run_id,
              content_hash=EXCLUDED.content_hash,
              payload_json=EXCLUDED.payload_json,
              generated_at=now()
        RETURNING id
        """,
        tenant_id, site_id, run_id, snapshot_day, digest,
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
    )
    totals = payload["totals"]
    return "completed", {
        "report_id": str(report_id),
        "crawl_job_id": str(crawl["id"]),
        "content_hash": digest,
        "declared_in_scope": totals["declared_in_scope"],
        "declared_not_crawled": totals["declared_not_crawled"],
        "indexable_missing_from_sitemap": totals["indexable_missing_from_sitemap"],
    }, None


async def _run_weekly_report(
    connection: Any, tenant_id: UUID, site_id: UUID, run_id: UUID, today: date
) -> tuple[str, dict[str, Any], str | None]:
    period_end = today - timedelta(days=1)
    period_start = period_end - timedelta(days=DIGEST_PERIOD_DAYS - 1)
    payload = await build_weekly_digest(connection, tenant_id, site_id, period_start, period_end)
    digest_hash = content_hash(payload)

    report_id = await connection.fetchval(
        """
        INSERT INTO report(
          tenant_id,site_id,routine_run_id,kind,period_start,period_end,
          content_hash,payload_json)
        VALUES($1,$2,$3,'weekly_digest',$4,$5,$6,$7::jsonb)
        ON CONFLICT(tenant_id,site_id,kind,period_start,period_end) DO UPDATE
          SET routine_run_id=EXCLUDED.routine_run_id,
              content_hash=EXCLUDED.content_hash,
              payload_json=EXCLUDED.payload_json,
              generated_at=now()
        RETURNING id
        """,
        tenant_id, site_id, run_id, period_start, period_end, digest_hash,
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
    )
    # Queue one delivery per enabled channel scoped to this site or the tenant.
    queued = await connection.fetchval(
        """
        WITH targets AS (
          SELECT id FROM notification_channel
          WHERE tenant_id=$1 AND enabled AND revoked_at IS NULL
            AND (site_id IS NULL OR site_id=$2)
        ), inserted AS (
          INSERT INTO notification_delivery(tenant_id,channel_id,report_id)
          SELECT $1,id,$3 FROM targets
          ON CONFLICT(channel_id,report_id) DO NOTHING
          RETURNING 1
        )
        SELECT count(*) FROM inserted
        """,
        tenant_id, site_id, report_id,
    )
    return "completed", {
        "report_id": str(report_id),
        "content_hash": digest_hash,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "notifications_queued": int(queued or 0),
    }, None


async def process_run(
    connection: Any,
    tenant_id: UUID,
    run_id: UUID,
    today: date | None = None,
    encryption_key: bytes | None = None,
) -> None:
    row = await connection.fetchrow(CLAIM_RUN_SQL, run_id, tenant_id, LEASE_MINUTES)
    if row is None:
        return
    routine_id: UUID = row["routine_id"]
    site_id: UUID = row["site_id"]
    kind: str = row["kind"]

    if kind in UNIMPLEMENTED_KINDS:
        await _finish(
            connection, run_id, tenant_id, routine_id, "skipped",
            skip_reason="routine_kind_not_implemented",
        )
        return

    try:
        async with connection.transaction():
            if kind == "site_audit":
                status, summary, skip = await _run_site_audit(
                    connection, tenant_id, site_id, routine_id
                )
            elif kind == "keyword_refresh":
                status, summary, skip = await _run_keyword_refresh(
                    connection, tenant_id, site_id, run_id,
                    today or datetime.now(UTC).date(), encryption_key,
                )
            elif kind == "sitemap_coverage":
                status, summary, skip = await _run_sitemap_coverage(
                    connection, tenant_id, site_id, run_id
                )
            elif kind == "weekly_report":
                status, summary, skip = await _run_weekly_report(
                    connection, tenant_id, site_id, run_id,
                    today or datetime.now(UTC).date(),
                )
            else:
                status, summary, skip = "skipped", {}, "unsupported_routine_kind"
            await _finish(
                connection, run_id, tenant_id, routine_id, status,
                summary=summary, skip_reason=skip,
            )
    except ValueError as error:
        await _finish(
            connection, run_id, tenant_id, routine_id, "failed",
            error_code=str(error)[:80],
        )
    except Exception:
        logger.exception("routine run failed", extra={"routine_kind": kind})
        await _finish(
            connection, run_id, tenant_id, routine_id, "failed", error_code="routine_run_error"
        )


async def run_routine_consumer(
    pool: Pool, streams: Stream, consumer: str, encryption_key: bytes | None = None
) -> None:
    try:
        await streams.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise
    while True:
        reclaimed = _messages(
            await streams.xautoclaim(
                STREAM, GROUP, consumer, min_idle_time=120_000, start_id="0-0", count=1
            ),
            True,
        )
        messages = reclaimed or _messages(
            await streams.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=5_000)
        )
        for message_id, fields in messages:
            if fields.get("type") != "routine.run.queued.v1":
                await streams.xack(STREAM, GROUP, message_id)
                continue
            try:
                async with pool.acquire() as connection:
                    await process_run(
                        connection,
                        UUID(fields["tenant_id"]),
                        UUID(fields["aggregate_id"]),
                        encryption_key=encryption_key,
                    )
                await streams.xack(STREAM, GROUP, message_id)
            except (KeyError, ValueError):
                logger.exception("invalid routine event", extra={"event_id": message_id})
                await streams.xack(STREAM, GROUP, message_id)
            except Exception:
                logger.exception("routine consumption failed", extra={"event_id": message_id})
