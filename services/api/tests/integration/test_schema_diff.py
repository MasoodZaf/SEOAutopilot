"""Detecting a schema that does not match its migrations.

The ledger records which files a database has run. It cannot record whether they
had their effect, and `--adopt-through` records files as applied on an
operator's word. That word was wrong by one file on 2026-09-07: migration 0034
widens two CHECK constraints, had never run on production, and was adopted as
done. Nothing noticed until the GA4 connector -- shipped, tested and deployed --
answered 500 on its first real request.

So these prove the detector notices the exact shape of that drift, rather than
only agreeing with a database it just built.
"""

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.cli.migrate import migrate
from app.cli.schema_diff import compare
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

MIGRATIONS = Path(__file__).resolve().parents[4] / "infra" / "migrations"


@pytest_asyncio.fixture
async def migrated_database(database_url):
    """A database built from the migrations, dropped afterwards."""
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    name = f"drift_test_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    base, _, _ = dsn.rpartition("/")
    target = f"{base}/{name}"
    await migrate(target, MIGRATIONS)
    yield target
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await admin.close()


async def _execute(dsn: str, statement: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


async def test_a_database_built_from_the_migrations_matches_them(migrated_database) -> None:
    drift = await compare(migrated_database, MIGRATIONS)
    assert drift.clean, (
        f"missing columns {drift.missing_columns}, missing checks {drift.missing_checks}"
    )


async def test_a_constraint_that_never_widened_is_caught(migrated_database) -> None:
    """Exactly what happened on production, reproduced.

    A CHECK left at its older, narrower definition is invisible to the ledger
    and to a column comparison. It shows up when a value the code considers
    valid is refused by the database, which on production was a 500 on the
    first real GA4 request.
    """
    await _execute(
        migrated_database,
        "ALTER TABLE connector DROP CONSTRAINT connector_type_check;"
        " ALTER TABLE connector ADD CONSTRAINT connector_type_check"
        " CHECK(type IN('google_search_console','dns_provider','github_repository'))",
    )

    drift = await compare(migrated_database, MIGRATIONS)

    assert not drift.clean
    assert any("google_analytics" in entry for entry in drift.missing_checks)
    assert any("connector_type_check" in entry for entry in drift.unexpected_checks)
    # The columns are untouched, which is why a column-only comparison would
    # have reported everything as fine.
    assert drift.missing_columns == []


async def test_a_missing_column_is_caught(migrated_database) -> None:
    await _execute(migrated_database, "ALTER TABLE site DROP COLUMN required_approver_count")

    drift = await compare(migrated_database, MIGRATIONS)

    assert "site.required_approver_count" in drift.missing_columns


async def test_a_column_nothing_asked_for_is_caught(migrated_database) -> None:
    """Drift in the other direction: something applied by hand and never recorded."""
    await _execute(migrated_database, "ALTER TABLE site ADD COLUMN applied_by_hand text")

    drift = await compare(migrated_database, MIGRATIONS)

    assert "site.applied_by_hand" in drift.unexpected_columns
