"""Applying only what a database is missing, proved on a real one.

The deployment guide has always carried this warning: the migration loop works
on a fresh database only, because the files use bare `CREATE TABLE`, so an
operator has to name the new range by hand on every incremental deploy. That is
correct exactly as often as somebody counts correctly under deploy pressure, and
both failure modes are quiet -- a migration skipped, or a range aborted halfway.

These run the whole schema into a database created for the purpose, because the
properties worth having are about the second run, not the first.
"""

import hashlib
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.cli.migrate import Migration, MigrationError, migrate, plan
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

MIGRATIONS = Path(__file__).resolve().parents[4] / "infra" / "migrations"


def _admin_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest_asyncio.fixture
async def scratch_database(database_url):
    """A database of its own, created and dropped around the test.

    The shared test database is rebuilt from these same files by the suite's
    own fixture; running the runner against it would fight that.
    """
    name = f"migrate_test_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_admin_dsn(database_url))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    base, _, _ = _admin_dsn(database_url).rpartition("/")
    yield f"{base}/{name}"
    admin = await asyncpg.connect(_admin_dsn(database_url))
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await admin.close()


async def _tables(dsn: str) -> set[str]:
    connection = await asyncpg.connect(dsn)
    try:
        rows = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        return {row["tablename"] for row in rows}
    finally:
        await connection.close()


async def test_a_fresh_database_gets_every_migration_once(scratch_database) -> None:
    first = await migrate(scratch_database, MIGRATIONS)
    assert len(first) == len(sorted(MIGRATIONS.glob("*.sql")))

    tables = await _tables(scratch_database)
    # A sample across the whole history, including the two newest.
    assert {"tenant", "site", "proposal", "app_user", "analytics_metric"} <= tables
    assert "schema_migration" in tables


async def test_running_it_again_does_nothing_at_all(scratch_database) -> None:
    """The property the hand-counted range never had."""
    await migrate(scratch_database, MIGRATIONS)
    assert await migrate(scratch_database, MIGRATIONS) == []


async def test_a_migration_edited_after_it_ran_is_refused_by_name(scratch_database) -> None:
    """The database and the repository would otherwise disagree in silence.

    Every later reader trusts the file to describe the schema, so a file that
    changed after it was applied makes the repository a wrong description of
    the database rather than a stale one.
    """
    await migrate(scratch_database, MIGRATIONS)
    connection = await asyncpg.connect(scratch_database)
    try:
        await connection.execute(
            "UPDATE schema_migration SET checksum = $1 WHERE filename = $2",
            "0" * 64,
            "0001_foundation.sql",
        )
    finally:
        await connection.close()

    with pytest.raises(MigrationError, match="edited after being applied"):
        await migrate(scratch_database, MIGRATIONS)


async def test_a_database_ahead_of_this_checkout_is_refused(scratch_database) -> None:
    """Usually an older deploy landing on a newer database."""
    await migrate(scratch_database, MIGRATIONS)
    connection = await asyncpg.connect(scratch_database)
    try:
        await connection.execute(
            "INSERT INTO schema_migration(filename, checksum) VALUES($1, $2)",
            "9999_from_the_future.sql",
            "f" * 64,
        )
    finally:
        await connection.close()

    with pytest.raises(MigrationError, match="does not have"):
        await migrate(scratch_database, MIGRATIONS)


async def test_adoption_records_an_existing_schema_without_replaying_it(
    scratch_database,
) -> None:
    """The path for the database that already has these tables.

    Production has 37 migrations applied and no ledger. Replaying them would
    fail on the first `CREATE TABLE`; adoption records them as done, and the
    next run applies only what is genuinely new. So this reproduces that
    database exactly -- the files applied, no ledger -- rather than adopting
    against an empty schema, where the assertion would pass for the wrong
    reason.
    """
    connection = await asyncpg.connect(scratch_database)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if int(path.name[:4]) > 34:
                break
            await connection.execute(path.read_text())
    finally:
        await connection.close()

    adopted = await migrate(scratch_database, MIGRATIONS, adopt_through=34)
    assert "0034_google_analytics_connector.sql" in adopted
    assert "0035_identity_and_membership.sql" not in adopted
    # Nothing was replayed: the tables those files created are untouched.
    assert "tenant" in await _tables(scratch_database)

    ran = await migrate(scratch_database, MIGRATIONS)
    assert ran == [
        "0035_identity_and_membership.sql",
        "0036_rollback_reconciliation.sql",
        "0037_analytics_ingestion.sql",
    ]
    assert {"app_user", "analytics_metric"} <= await _tables(scratch_database)


async def test_adoption_is_refused_once_anything_is_recorded(scratch_database) -> None:
    await migrate(scratch_database, MIGRATIONS, adopt_through=1)
    with pytest.raises(MigrationError, match="already has rows"):
        await migrate(scratch_database, MIGRATIONS, adopt_through=34)


async def test_a_dry_run_reports_without_touching_the_database(scratch_database) -> None:
    pending = await migrate(scratch_database, MIGRATIONS, dry_run=True)
    assert pending, "a fresh database has everything pending"
    assert await _tables(scratch_database) == {"schema_migration"}


def test_the_plan_is_ordered_and_excludes_what_is_recorded() -> None:
    migrations = [
        Migration(MIGRATIONS / "0001_foundation.sql", "a"),
        Migration(MIGRATIONS / "0002_verification_and_crawls.sql", "b"),
    ]
    assert [item.filename for item in plan(migrations, {})] == [
        "0001_foundation.sql",
        "0002_verification_and_crawls.sql",
    ]
    assert [item.filename for item in plan(migrations, {"0001_foundation.sql": "a"})] == [
        "0002_verification_and_crawls.sql"
    ]


def test_every_migration_in_the_repository_has_a_unique_sequence() -> None:
    """Two files at one position have no defined order; the sort decides silently."""
    from app.cli.migrate import discover

    migrations = discover(MIGRATIONS)
    sequences = [item.sequence for item in migrations]
    assert len(sequences) == len(set(sequences))
    assert sequences == sorted(sequences)
    for item in migrations:
        assert item.checksum == hashlib.sha256(item.path.read_bytes()).hexdigest()
