"""Checking the things that fail quietly.

The API answers /health and the worker exposes counters, and neither notices the
failures this system actually has. Every one of these has already happened here
or is one restart away, and all of them look exactly like normal operation from
outside: a run leased to a worker that died, an outbox nobody is draining, a
connector that has been asking for re-consent since Tuesday, a rollback waiting
on a revert nobody will merge, a backup directory whose newest file is three
weeks old.

Each check reports a count and a threshold. Exceeding one exits non-zero, so a
systemd timer turns it into a failed unit and a journal entry rather than a
number on a dashboard nobody opens. It reads across tenants, so it runs on the
relay identity -- the role migration 0027 created for sweeps -- and it reads
only counts and ages, never content.

    python -m app.cli.health --database-url "$RELAY_DATABASE_URL"
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    value: int
    threshold: int
    detail: str

    @property
    def failing(self) -> bool:
        return self.value > self.threshold

    def __str__(self) -> str:
        mark = "FAIL" if self.failing else "ok"
        return f"[{mark}] {self.name}: {self.value} (allowed {self.threshold}) — {self.detail}"


# A lease that expired is not work in progress; it is work whose worker is gone.
# The reaper exists to return these, so a growing number means the reaper is not
# running rather than that the jobs are slow.
STALE_LEASE_SQL = """
SELECT
  (SELECT count(*) FROM routine_run
     WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until < now()) +
  (SELECT count(*) FROM connector_sync
     WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until < now())
"""

# Rows the relay should have dispatched. A backlog means nothing is draining the
# outbox, and every consumer downstream is idle for a reason nobody can see.
OUTBOX_BACKLOG_SQL = """
SELECT count(*) FROM outbox_event
WHERE published_at IS NULL AND occurred_at < now() - make_interval(mins => $1)
"""

REAUTH_SQL = """
SELECT count(*) FROM connector
WHERE status IN ('reauthorization_required', 'error')
"""

# A rollback whose revert nobody has merged or closed. The reconciler records
# what it found; this notices that nothing has changed for a long time, which is
# a decision somebody owes rather than a fault.
STALE_ROLLBACK_SQL = """
SELECT count(*) FROM rollback_receipt
WHERE status = 'pending' AND rolled_back_at < now() - make_interval(days => $1)
"""

# Nothing has looked at these at all, which is different from "looked, still
# open" and means the reconciler is not running.
UNRECONCILED_SQL = """
SELECT count(*) FROM rollback_receipt
WHERE status = 'pending' AND reconciled_at IS NULL
  AND rolled_back_at < now() - interval '1 hour'
"""


async def run_checks(
    database_url: str,
    *,
    backup_dir: Path | None,
    backup_max_age_hours: int,
    outbox_backlog_minutes: int,
    stale_rollback_days: int,
) -> list[Check]:
    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        stale_leases = await connection.fetchval(STALE_LEASE_SQL)
        backlog = await connection.fetchval(OUTBOX_BACKLOG_SQL, outbox_backlog_minutes)
        reauth = await connection.fetchval(REAUTH_SQL)
        stale_rollbacks = await connection.fetchval(STALE_ROLLBACK_SQL, stale_rollback_days)
        unreconciled = await connection.fetchval(UNRECONCILED_SQL)
    finally:
        await connection.close()

    checks = [
        Check(
            "stale_leases",
            int(stale_leases or 0),
            0,
            "runs leased to a worker that is gone; the reaper should return these",
        ),
        Check(
            "outbox_backlog",
            int(backlog or 0),
            0,
            f"events unpublished for over {outbox_backlog_minutes} minutes; nothing is draining the outbox",
        ),
        Check(
            "connectors_needing_attention",
            int(reauth or 0),
            0,
            "connectors in reauthorization_required or error; a person has to re-consent",
        ),
        Check(
            "unreconciled_rollbacks",
            int(unreconciled or 0),
            0,
            "pending rollbacks nothing has ever checked; the reconciler is not running",
        ),
        Check(
            "rollbacks_awaiting_a_decision",
            int(stale_rollbacks or 0),
            0,
            f"reverts open for over {stale_rollback_days} days; the change is still live",
        ),
    ]
    if backup_dir is not None:
        checks.append(_backup_age(backup_dir, backup_max_age_hours))
    return checks


def _backup_age(directory: Path, max_age_hours: int) -> Check:
    """How old the newest verified dump is.

    A backup directory that stopped being written to looks exactly like one that
    is working, right up until it is needed.
    """
    dumps = sorted(directory.glob("seo_autopilot_*.dump"), key=lambda path: path.stat().st_mtime)
    if not dumps:
        return Check("backup_age_hours", max_age_hours + 1, max_age_hours, f"no dumps in {directory}")
    newest = dumps[-1]
    age = datetime.now(UTC) - datetime.fromtimestamp(newest.stat().st_mtime, UTC)
    hours = int(age.total_seconds() // 3600)
    return Check("backup_age_hours", hours, max_age_hours, f"newest dump is {newest.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("RELAY_DATABASE_URL") or os.environ.get("DATABASE_URL"),
        help="Reads across tenants, so this wants the relay role.",
    )
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--backup-max-age-hours", type=int, default=36)
    parser.add_argument("--outbox-backlog-minutes", type=int, default=15)
    parser.add_argument("--stale-rollback-days", type=int, default=7)
    arguments = parser.parse_args()
    if not arguments.database_url:
        print("refused: --database-url or RELAY_DATABASE_URL is required")
        return 2

    checks = asyncio.run(
        run_checks(
            arguments.database_url,
            backup_dir=arguments.backup_dir,
            backup_max_age_hours=arguments.backup_max_age_hours,
            outbox_backlog_minutes=arguments.outbox_backlog_minutes,
            stale_rollback_days=arguments.stale_rollback_days,
        )
    )
    for check in checks:
        print(check)
    failing = [check for check in checks if check.failing]
    if failing:
        print(f"\n{len(failing)} check(s) failing: {', '.join(check.name for check in failing)}")
        return 1
    print(f"\nall {len(checks)} checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
