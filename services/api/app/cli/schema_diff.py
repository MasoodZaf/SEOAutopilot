"""Does the live schema match what the migrations say it should be?

The ledger records which migration files a database has *run*. It cannot record
whether they had their intended effect, and `--adopt-through` records files as
applied on an operator's word alone. On 2026-09-07 that word was wrong by one
file: migration 0034 widens two CHECK constraints, it had never run on
production, and adoption recorded it as done. Nothing noticed until the GA4
connector -- shipped, tested, and deployed -- answered 500 on its first real
request, because the constraint still refused the value.

So this stops trusting the ledger and asks the database. It builds a reference
schema by running every migration into a scratch database, then compares that
against the live one: every column, and every CHECK constraint. Those are what
migrations after the initial schema mostly change, and a CHECK that silently
never widened is exactly the drift a ledger cannot see.

    python -m app.cli.schema_diff --database-url "$OWNER_URL"

Exits non-zero on any difference, so it can run beside the other invariants.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from app.cli.migrate import MigrationError, migrate

COLUMNS_SQL = """
SELECT table_name || '.' || column_name
FROM information_schema.columns
WHERE table_schema = 'public'
"""

CHECKS_SQL = """
SELECT c.conname || ' :: ' || pg_get_constraintdef(c.oid)
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND c.contype = 'c'
"""


@dataclass(frozen=True, slots=True)
class Drift:
    missing_columns: list[str] = field(default_factory=list)
    unexpected_columns: list[str] = field(default_factory=list)
    missing_checks: list[str] = field(default_factory=list)
    unexpected_checks: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (
            self.missing_columns
            or self.unexpected_columns
            or self.missing_checks
            or self.unexpected_checks
        )


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _facts(dsn: str) -> tuple[set[str], set[str]]:
    connection = await asyncpg.connect(dsn)
    try:
        columns = {row[0] for row in await connection.fetch(COLUMNS_SQL)}
        checks = {row[0] for row in await connection.fetch(CHECKS_SQL)}
        return columns, checks
    finally:
        await connection.close()


async def compare(database_url: str, directory: Path) -> Drift:
    """Build a reference from the migration files, then diff the live database."""
    dsn = _dsn(database_url)
    reference = f"schema_reference_{secrets.token_hex(6)}"
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{reference}"')
    finally:
        await admin.close()

    base, _, _ = dsn.rpartition("/")
    reference_dsn = f"{base}/{reference}"
    try:
        await migrate(reference_dsn, directory)
        expected_columns, expected_checks = await _facts(reference_dsn)
        live_columns, live_checks = await _facts(dsn)
    finally:
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
                reference,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{reference}"')
        finally:
            await admin.close()

    return Drift(
        missing_columns=sorted(expected_columns - live_columns),
        unexpected_columns=sorted(live_columns - expected_columns),
        missing_checks=sorted(expected_checks - live_checks),
        unexpected_checks=sorted(live_checks - expected_checks),
    )


def _report(drift: Drift) -> None:
    for label, entries in (
        ("missing from the live database", drift.missing_columns),
        ("present live but not in the migrations", drift.unexpected_columns),
    ):
        for entry in entries:
            print(f"[column]     {label}: {entry}")
    for label, entries in (
        ("missing from the live database", drift.missing_checks),
        ("present live but not in the migrations", drift.unexpected_checks),
    ):
        for entry in entries:
            print(f"[constraint] {label}: {entry}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("MIGRATION_DATABASE_URL"),
        help="The owning role: this creates and drops a scratch database.",
    )
    parser.add_argument("--directory", default="/app/migrations", type=Path)
    arguments = parser.parse_args()
    if not arguments.database_url:
        print("refused: --database-url or MIGRATION_DATABASE_URL is required")
        return 2
    try:
        drift = asyncio.run(compare(arguments.database_url, arguments.directory))
    except MigrationError as error:
        print(f"refused: {error}")
        return 2
    if drift.clean:
        print("schema matches the migrations")
        return 0
    _report(drift)
    print("\nthe live schema does not match the migrations")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
