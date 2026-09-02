"""Claims due routines and queues one run per scheduled slot.

The scheduler never performs the work itself and never deploys. It records an
auditable `routine_run` row, advances the schedule, and hands execution to the
runner through the existing outbox. Safety gates are evaluated here so that a
blocked site still leaves a `skipped` record with a reason.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from app.routines.schedule import advance_from_slot, schedule_from_row

logger = logging.getLogger(__name__)

CLAIM_BATCH = 25
IDLE_SLEEP_SECONDS = 30.0
BUSY_SLEEP_SECONDS = 1.0

# A routine that keeps failing is parked rather than retried forever.
MAX_CONSECUTIVE_FAILURES = 5


class DatabaseConnection(Protocol):
    def transaction(self) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> str: ...


class Pool(Protocol):
    def acquire(self) -> Any: ...


DUE_ROUTINES_SQL = """
SELECT r.id, r.tenant_id, r.site_id, r.kind, r.cadence,
       r.schedule_hour_utc, r.schedule_minute_utc, r.schedule_isodow, r.schedule_dom,
       r.next_run_at, r.consecutive_failures,
       s.status AS site_status, s.verified_at, s.emergency_freeze
FROM routine r
JOIN site s ON s.id = r.site_id AND s.tenant_id = r.tenant_id
WHERE r.enabled AND r.next_run_at <= $1
ORDER BY r.next_run_at
FOR UPDATE OF r SKIP LOCKED
LIMIT $2
"""


def skip_reason_for(row: Any) -> str | None:
    """Fail closed: a routine must not become a path around site governance."""
    if row["site_status"] != "active" or row["verified_at"] is None:
        return "site_not_verified"
    if row["emergency_freeze"]:
        return "site_frozen"
    if row["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
        return "routine_parked_after_failures"
    return None


async def claim_due_routines(
    connection: DatabaseConnection, now: datetime | None = None
) -> int:
    """Queue one run per due routine. Returns the number of rows claimed."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    claimed = 0
    async with connection.transaction():
        rows = await connection.fetch(DUE_ROUTINES_SQL, moment, CLAIM_BATCH)
        for row in rows:
            slot = row["next_run_at"]
            schedule = schedule_from_row(row)
            following = advance_from_slot(schedule, slot, moment)
            reason = skip_reason_for(row)

            run_id = await connection.fetchval(
                """
                INSERT INTO routine_run(
                  tenant_id,routine_id,site_id,kind,status,trigger,scheduled_for,
                  finished_at,skip_reason
                )
                VALUES($1,$2,$3,$4,$5,'schedule',$6,$7,$8)
                ON CONFLICT(routine_id,scheduled_for) DO NOTHING
                RETURNING id
                """,
                row["tenant_id"],
                row["id"],
                row["site_id"],
                row["kind"],
                "skipped" if reason else "queued",
                slot,
                moment if reason else None,
                reason,
            )
            await connection.execute(
                """
                UPDATE routine
                SET next_run_at=$3, last_run_at=$4, last_status=COALESCE($5,last_status),
                    updated_at=now()
                WHERE id=$1 AND tenant_id=$2
                """,
                row["id"],
                row["tenant_id"],
                following,
                moment,
                "skipped" if reason else None,
            )
            if run_id is None:
                # Another replica already queued this slot.
                continue
            claimed += 1
            if reason:
                logger.info(
                    "routine slot skipped",
                    extra={"routine_kind": row["kind"], "skip_reason": reason},
                )
                continue
            await connection.execute(
                """
                INSERT INTO outbox_event(
                  tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload
                )
                VALUES($1,'routine.run.queued.v1',1,'routine_run',$2,$3::jsonb)
                """,
                row["tenant_id"],
                run_id,
                json.dumps(
                    {
                        "routine_run_id": str(run_id),
                        "routine_id": str(row["id"]),
                        "site_id": str(row["site_id"]),
                        "kind": row["kind"],
                        "trigger": "schedule",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
    return claimed


async def run_scheduler(pool: Pool, sleep: Any = None) -> None:
    import asyncio

    pause = sleep or asyncio.sleep
    while True:
        try:
            async with pool.acquire() as connection:
                claimed = await claim_due_routines(connection)
            await pause(BUSY_SLEEP_SECONDS if claimed else IDLE_SLEEP_SECONDS)
        except Exception:
            logger.exception("routine scheduler tick failed")
            await pause(IDLE_SLEEP_SECONDS)
