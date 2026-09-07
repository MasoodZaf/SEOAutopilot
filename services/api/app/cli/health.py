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

# A tenant nobody can administer.
#
# `MembershipService` refuses to remove or demote a last owner, and locks the
# rows it counts so two concurrent demotions cannot each believe the other
# survives. That protects the transition. It says nothing about a tenant that
# arrived in this state another way -- a suspension applied directly, a restore
# from a dump taken mid-change, an owner whose row was written before those
# rules existed. The result is a tenant whose members cannot be changed by
# anybody in it, recoverable only by an operator on the host, and it looks like
# normal operation until somebody needs to grant access.
OWNERLESS_TENANT_SQL = """
SELECT count(*) FROM tenant t
WHERE EXISTS (SELECT 1 FROM tenant_membership m WHERE m.tenant_id = t.id)
  AND NOT EXISTS (
    SELECT 1 FROM tenant_membership m
    WHERE m.tenant_id = t.id AND m.role = 'owner' AND m.status = 'active'
  )
"""

# A connector that says it is fine and has not read anything in days.
#
# `connectors_needing_attention` catches the honest failures: the provider
# refused, the connector moved to `error` or `reauthorization_required`, and
# somebody has to re-consent. This catches the quiet one -- `active`, no error,
# and simply not syncing, because the routine was never enabled, or was
# disabled, or the site was archived and the connector left behind.
#
# It is the shape of the risk the GA4 connector carries from today: an
# authorization that returns no refresh token works perfectly for one hour and
# then stops, and nothing about the connector row changes to say so.
STALE_CONNECTOR_SQL = """
SELECT count(*) FROM connector
WHERE status = 'active'
  AND type IN ('google_search_console', 'google_analytics')
  AND (last_sync_at IS NULL OR last_sync_at < now() - make_interval(days => $1))
  AND created_at < now() - make_interval(days => $1)
"""


async def run_checks(
    database_url: str,
    *,
    backup_dir: Path | None,
    backup_max_age_hours: int,
    outbox_backlog_minutes: int,
    stale_rollback_days: int,
    stale_connector_days: int,
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
        ownerless = await connection.fetchval(OWNERLESS_TENANT_SQL)
        stale_connectors = await connection.fetchval(STALE_CONNECTOR_SQL, stale_connector_days)
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
            "tenants_without_an_owner",
            int(ownerless or 0),
            0,
            "a tenant whose membership nobody in it can change; needs an operator",
        ),
        Check(
            "connectors_not_syncing",
            int(stale_connectors or 0),
            0,
            f"active connectors that have read nothing for {stale_connector_days} days",
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
    # Three days rather than one: a daily sync that misses a single run has
    # not failed, and a check that cries on every transient outage gets muted,
    # which is the same as not having it.
    parser.add_argument("--stale-connector-days", type=int, default=3)
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
            stale_connector_days=arguments.stale_connector_days,
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
