"""Return work whose lease expired to the queue, or record that it failed.

Every leased consumer can re-claim a run whose lease has expired -- but only
while a stream message is prompting it. Nothing re-reads the database on its
own. So a run whose message was acked, trimmed or lost while the row still said
'running' has no route back: the lease expires, the reclaim condition becomes
true, and no one ever evaluates it. The row stays 'running' forever, the site it
belongs to quietly stops being measured, and the API reports a run in progress.

That is not hypothetical. A PageSpeed run was found in exactly this state, with
no pending Redis entry despite the failing branch never acking it.

This sweep is the missing half. It runs across tenants on the relay identity,
finds rows whose lease expired well past the grace period, and either
republishes the event that re-delivers them or, when their attempts are spent,
writes the failure down so it is visible instead of pending forever.

It is deliberately no more aggressive than the consumers themselves: they
already treat an expired lease as another consumer's abandoned work, and the
grace period on top means a live consumer between heartbeats is never disturbed.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# How long past its expiry a lease must sit before the row is considered
# abandoned. Leases run 2-5 minutes, so this is several missed renewals rather
# than a slow one.
GRACE_SECONDS = 300

SWEEP_INTERVAL_SECONDS = 60


class Connection(Protocol):
    def transaction(self) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def execute(self, query: str, *args: Any) -> str: ...


class Pool(Protocol):
    def acquire(self) -> Any: ...


@dataclass(frozen=True)
class LeasedWork:
    """One table of leased work and the event that re-delivers a row from it."""

    table: str
    event_type: str
    aggregate_type: str
    #: Column bounding retries, or None when the table does not track them.
    attempts_column: str | None
    max_attempts: int


# The four stream-driven work tables. notification_delivery is deliberately
# absent: its dispatcher sweeps the database directly on every tick, so an
# expired lease there already heals itself.
LEASED_WORK: tuple[LeasedWork, ...] = (
    LeasedWork("crawl_job", "crawl.requested", "crawl_job", "attempts", 3),
    LeasedWork("performance_run", "performance.requested", "performance_run", "attempts", 3),
    LeasedWork("routine_run", "routine.run.queued.v1", "routine_run", "attempts", 3),
    # connector_sync tracks no attempt count, so there is no safe bound on
    # requeueing it. An abandoned sync is failed instead; its cursor is
    # preserved, so a fresh sync resumes rather than restarting.
    LeasedWork("connector_sync", "connector.sync_requested", "connector_sync", None, 0),
)

_EXPIRED = "status='running' AND lease_until IS NOT NULL AND lease_until < now() - ($1 * interval '1 second')"


def requeue_sql(work: LeasedWork) -> str:
    """Reset abandoned rows to queued and republish their delivery event.

    One statement, so a row can never be queued without the event that would
    pick it up, nor an event emitted for a row that stayed running.
    """
    assert work.attempts_column is not None
    return f"""
    WITH abandoned AS (
      UPDATE {work.table}
      SET status='queued', lease_until=NULL
      WHERE {_EXPIRED} AND {work.attempts_column} < $2
      RETURNING id, tenant_id
    )
    INSERT INTO outbox_event(
      tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload
    )
    SELECT tenant_id, $3, 1, $4, id, $5::jsonb
    FROM abandoned
    RETURNING aggregate_id
    """


def fail_sql(work: LeasedWork) -> str:
    """Terminally fail abandoned rows that have no retries left."""
    exhausted = (
        f"{work.attempts_column} >= $2" if work.attempts_column is not None else "true"
    )
    return f"""
    UPDATE {work.table}
    SET status='failed', finished_at=now(), lease_until=NULL, error_code='lease_expired'
    WHERE {_EXPIRED} AND {exhausted}
    RETURNING id
    """


async def sweep_once(
    connection: Connection, *, grace_seconds: int = GRACE_SECONDS
) -> dict[str, int]:
    """Requeue or fail every abandoned row. Returns counts per outcome."""
    requeued = 0
    failed = 0
    for work in LEASED_WORK:
        taken = 0
        async with connection.transaction():
            if work.attempts_column is not None:
                rows = await connection.fetch(
                    requeue_sql(work),
                    grace_seconds,
                    work.max_attempts,
                    work.event_type,
                    work.aggregate_type,
                    json.dumps({"reason": "lease_expired"}, separators=(",", ":")),
                )
                requeued += len(rows)
                taken += len(rows)
                dead = await connection.fetch(
                    fail_sql(work), grace_seconds, work.max_attempts
                )
            else:
                # No attempts column means fail_sql takes no $2 to bind.
                dead = await connection.fetch(fail_sql(work), grace_seconds)
            failed += len(dead)
            taken += len(dead)
        if taken:
            logger.warning(
                "reclaimed abandoned work",
                extra={"work_table": work.table, "row_count": taken},
            )
    return {"requeued": requeued, "failed": failed}


async def run_reaper(
    pool: Pool,
    *,
    interval_seconds: float = SWEEP_INTERVAL_SECONDS,
    sleep: Any = None,
) -> None:
    pause = sleep or asyncio.sleep
    while True:
        try:
            async with pool.acquire() as connection:
                await sweep_once(connection)
        except Exception:
            logger.exception("lease reaper sweep failed")
        await pause(interval_seconds)
