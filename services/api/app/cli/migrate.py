"""Applying only the migrations a database has not already had.

DEPLOYMENT.md carried this warning for as long as there have been migrations:

    **The migration loop in §3 only works on a fresh database.** The files use
    bare `CREATE TABLE`, so re-running an applied migration aborts the loop.
    Name the new range explicitly on an incremental deploy.

Naming the range by hand is the whole problem. It is correct exactly as often as
somebody counts correctly under deploy pressure, the failure modes are silence
(a migration skipped, so the schema and the code disagree) and an aborted deploy
halfway through a range. Neither is discoverable until something else breaks.

So the database records what it has applied, and this runs what is missing. Two
properties are worth more than the convenience:

* A file already applied whose contents have since changed is refused by name.
  Editing a migration after it has run means the database and the repository
  disagree about what the schema is, and every later reader trusts the file.
* Nothing is applied out of order, and one failure stops the run rather than
  continuing to the next file.

The migrations create and alter tables, so this connects as the owning role --
not `seo_autopilot_app`, which holds DML only. The URL is therefore explicit
rather than read from the application's settings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import asyncpg

LEDGER = "schema_migration"

LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER} (
  filename text PRIMARY KEY,
  checksum text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""

SEQUENCE = re.compile(r"^(\d{4})_")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    path: Path
    checksum: str

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def sequence(self) -> int:
        match = SEQUENCE.match(self.path.name)
        if match is None:
            raise MigrationError(f"{self.path.name} does not start with a four-digit sequence")
        return int(match.group(1))


def discover(directory: Path) -> list[Migration]:
    # Dotfiles are not migrations. macOS writes AppleDouble `._name.sql`
    # alongside real files and rsync carries them onto the host, where they
    # match `*.sql` and look like a migration numbered nothing.
    files = sorted(
        path for path in directory.glob("*.sql") if not path.name.startswith(".")
    )
    if not files:
        raise MigrationError(f"no migrations found in {directory}")
    migrations = [
        Migration(path, hashlib.sha256(path.read_bytes()).hexdigest()) for path in files
    ]
    sequences = [item.sequence for item in migrations]
    duplicates = {value for value in sequences if sequences.count(value) > 1}
    if duplicates:
        # Two files claiming one position have no defined order between them,
        # and whichever sorts first wins silently.
        raise MigrationError(f"duplicate migration sequence(s): {sorted(duplicates)}")
    return migrations


def plan(migrations: list[Migration], applied: dict[str, str]) -> list[Migration]:
    """What is left to run, refusing a file that changed after it ran."""
    changed = [
        item.filename
        for item in migrations
        if item.filename in applied and applied[item.filename] != item.checksum
    ]
    if changed:
        raise MigrationError(
            "these migrations were edited after being applied, so the database and the "
            f"repository disagree about the schema: {', '.join(changed)}"
        )
    missing = sorted(set(applied) - {item.filename for item in migrations})
    if missing:
        # The database has run something this checkout does not contain, which
        # usually means an older deploy against a newer database.
        raise MigrationError(
            f"the database has applied migrations this checkout does not have: {', '.join(missing)}"
        )
    return [item for item in migrations if item.filename not in applied]


async def _applied(connection: asyncpg.Connection) -> dict[str, str]:
    await connection.execute(LEDGER_DDL)
    rows = await connection.fetch(f"SELECT filename, checksum FROM {LEDGER}")
    return {row["filename"]: row["checksum"] for row in rows}


async def _record(connection: asyncpg.Connection, migration: Migration) -> None:
    await connection.execute(
        f"INSERT INTO {LEDGER}(filename, checksum) VALUES($1, $2)",
        migration.filename,
        migration.checksum,
    )


async def migrate(
    database_url: str,
    directory: Path,
    *,
    dry_run: bool = False,
    adopt_through: int | None = None,
) -> list[str]:
    """Apply what is pending. Returns the filenames it ran, or would run."""
    migrations = discover(directory)
    connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        applied = await _applied(connection)

        if adopt_through is not None:
            if applied:
                raise MigrationError(
                    "the ledger already has rows; adoption is only for a database whose "
                    "migrations were applied before there was a ledger"
                )
            adopted = [item for item in migrations if item.sequence <= adopt_through]
            if dry_run:
                return [item.filename for item in adopted]
            for item in adopted:
                await _record(connection, item)
            return [item.filename for item in adopted]

        pending = plan(migrations, applied)
        if dry_run:
            return [item.filename for item in pending]

        ran: list[str] = []
        for item in pending:
            # The file supplies its own transaction, or PostgreSQL wraps the
            # whole simple-query script in one. Either way a failure leaves
            # nothing half-applied.
            try:
                await connection.execute(item.path.read_text())
            except Exception as error:
                raise MigrationError(f"{item.filename} failed: {error}") from error
            await _record(connection, item)
            ran.append(item.filename)
        return ran
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("MIGRATION_DATABASE_URL"),
        help="The owning role's connection, not the application's; migrations alter schema.",
    )
    parser.add_argument("--directory", default="/app/migrations", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Report what would run.")
    parser.add_argument(
        "--adopt-through",
        type=int,
        help=(
            "Record every migration up to this sequence as applied without running it. "
            "For a database whose migrations were applied before this ledger existed. "
            "Refused once the ledger has any row."
        ),
    )
    parser.add_argument(
        "--mark-applied",
        help=(
            "Record one migration as applied without running it. Only for the narrow case "
            "where a migration ran but the process died before the ledger was written."
        ),
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        print("refused: --database-url or MIGRATION_DATABASE_URL is required")
        return 1
    try:
        if arguments.mark_applied:
            names = asyncio.run(
                _mark_applied(arguments.database_url, arguments.directory, arguments.mark_applied)
            )
        else:
            names = asyncio.run(
                migrate(
                    arguments.database_url,
                    arguments.directory,
                    dry_run=arguments.dry_run,
                    adopt_through=arguments.adopt_through,
                )
            )
    except MigrationError as error:
        print(f"refused: {error}")
        return 1
    verb = "would apply" if arguments.dry_run else "applied"
    print(f"{verb} {len(names)} migration(s)" + (": " + ", ".join(names) if names else ""))
    return 0


async def _mark_applied(database_url: str, directory: Path, filename: str) -> list[str]:
    migration = next(
        (item for item in discover(directory) if item.filename == filename), None
    )
    if migration is None:
        raise MigrationError(f"no migration named {filename}")
    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        applied = await _applied(connection)
        if migration.filename in applied:
            raise MigrationError(f"{filename} is already recorded")
        await _record(connection, migration)
        return [migration.filename]
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
