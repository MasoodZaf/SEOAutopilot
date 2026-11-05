"""Delete one workspace and everything that belongs to it.

The privacy policy promises this on request, and nothing else in the system can
do it: every foreign key to `tenant` is NO ACTION, so a bare `DELETE FROM tenant`
is refused and a hand-ordered script goes stale the day a table is added. The
order is therefore read from the catalog at run time -- every table with a
`tenant_id`, children before parents -- and a table added next month is covered
without anyone remembering this file.

It runs on the migration role because row-level security hides other tenants'
rows from the application role, and a delete that cannot see a row cannot
delete it either. One transaction: it removes everything or nothing.

    python -m app.cli.delete_workspace --tenant-id <uuid> --confirm-slug <slug>            # counts only
    python -m app.cli.delete_workspace --tenant-id <uuid> --confirm-slug <slug> --execute  # deletes

People's sign-in identities (`app_user`) are not workspace data and stay; a
person who belongs to no workspace any more is listed so an operator can act on
an account-deletion request separately. Nightly backups still hold the data for
their retention period, which the policy says.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import defaultdict
from uuid import UUID

import asyncpg

TENANT_TABLES_SQL = """
SELECT c.oid::regclass::text AS name
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname <> 'tenant'
"""

# child -> parent, among tables we delete from. Self-references are left out:
# one DELETE removes the whole set, and NO ACTION is checked at statement end.
EDGES_SQL = """
SELECT conrelid::regclass::text AS child, confrelid::regclass::text AS parent
FROM pg_constraint
WHERE contype = 'f' AND conrelid <> confrelid
"""

ORPHANED_USERS_SQL = """
SELECT u.id FROM app_user u
WHERE EXISTS (SELECT 1 FROM tenant_membership m WHERE m.user_id = u.id AND m.tenant_id = $1)
  AND NOT EXISTS (SELECT 1 FROM tenant_membership m WHERE m.user_id = u.id AND m.tenant_id <> $1)
"""


class DeletionOrderError(RuntimeError):
    pass


def deletion_order(tables: set[str], edges: list[tuple[str, str]]) -> list[str]:
    """Children before parents. A cycle is refused rather than guessed at."""
    parents_of: dict[str, set[str]] = defaultdict(set)
    children_of: dict[str, set[str]] = defaultdict(set)
    for child, parent in edges:
        if child in tables and parent in tables:
            parents_of[child].add(parent)
            children_of[parent].add(child)
    order: list[str] = []
    remaining = set(tables)
    while remaining:
        # A table is safe to empty once nothing still waiting refers to it.
        ready = sorted(t for t in remaining if not (children_of[t] & remaining))
        if not ready:
            raise DeletionOrderError(f"foreign keys form a cycle among: {sorted(remaining)}")
        order.extend(ready)
        remaining.difference_update(ready)
    return order


async def delete_workspace(
    database_url: str, tenant_id: UUID, confirm_slug: str, *, execute: bool
) -> tuple[dict[str, int], list[UUID]]:
    connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        slug = await connection.fetchval("SELECT slug FROM tenant WHERE id = $1", tenant_id)
        if slug is None:
            raise SystemExit(f"refused: no workspace {tenant_id}")
        if slug != confirm_slug:
            raise SystemExit(f"refused: workspace {tenant_id} is '{slug}', not '{confirm_slug}'")

        tables = {row["name"] for row in await connection.fetch(TENANT_TABLES_SQL)}
        edges = [(row["child"], row["parent"]) for row in await connection.fetch(EDGES_SQL)]
        order = deletion_order(tables, edges)
        orphaned = [row["id"] for row in await connection.fetch(ORPHANED_USERS_SQL, tenant_id)]

        counts: dict[str, int] = {}
        transaction = connection.transaction()
        await transaction.start()
        try:
            for table in order:
                # Table names come from the catalog, never from input.
                status = await connection.execute(
                    f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id
                )
                deleted = int(status.split()[-1])
                if deleted:
                    counts[table] = deleted
            await connection.execute("DELETE FROM tenant WHERE id = $1", tenant_id)
            counts["tenant"] = 1
        except BaseException:
            await transaction.rollback()
            raise
        if execute:
            await transaction.commit()
        else:
            # A dry run performs the real deletes and rolls them back, so the
            # counts are what --execute will remove, foreign keys included.
            await transaction.rollback()
        return counts, orphaned
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("MIGRATION_DATABASE_URL"))
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--confirm-slug", required=True, help="The workspace's slug, typed out.")
    parser.add_argument("--execute", action="store_true", help="Without this, nothing is deleted.")
    arguments = parser.parse_args()
    if not arguments.database_url:
        print("refused: --database-url or MIGRATION_DATABASE_URL is required")
        return 2

    counts, orphaned = asyncio.run(
        delete_workspace(
            arguments.database_url,
            arguments.tenant_id,
            arguments.confirm_slug,
            execute=arguments.execute,
        )
    )
    verb = "deleted" if arguments.execute else "would delete"
    for table, count in sorted(counts.items()):
        print(f"{verb} {count:>7} {table}")
    print(f"\n{verb} {sum(counts.values())} rows across {len(counts)} tables")
    if orphaned:
        print(f"{len(orphaned)} person(s) now belong to no workspace: {', '.join(map(str, orphaned))}")
    if not arguments.execute:
        print("dry run: nothing was deleted; add --execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
