"""Row-level security, proved against PostgreSQL rather than a mock.

`AGENTS.md` makes tenant isolation the primary boundary and `get_tenant_session`
sets `app.tenant_id` so row-level security can enforce it. Whether the database
actually enforces it has never been tested: every service test mocks the session,
so a policy that is enabled but inert reads exactly like one that works.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

TENANT_TABLES_SAMPLE = ["site", "proposal", "proposal_approval", "audit_event", "connector"]

# Isolation was inert until migration 0027 for two independent reasons, and both
# had to be fixed: no table set FORCE ROW LEVEL SECURITY, and the connecting role
# was a SUPERUSER, which ignores row security unconditionally and which FORCE
# does not apply to. Forcing alone was measured on a clean schema and changed
# nothing. These tests run as `seo_autopilot_app`, the NOSUPERUSER NOBYPASSRLS
# role the services use, because the property is not observable from a role that
# can bypass it.


async def test_every_tenant_table_forces_row_level_security(raw_session) -> None:
    """Enabling RLS is not enough: the table owner bypasses it unless it is forced.

    The API connects as the role that ran the migrations and therefore owns every
    table, so `ENABLE ROW LEVEL SECURITY` alone leaves the policies inert on the
    one connection that matters.
    """
    rows = (
        await raw_session.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND c.relkind = 'r'"
                " AND c.relrowsecurity AND NOT c.relforcerowsecurity"
                " ORDER BY c.relname"
            )
        )
    ).scalars()
    unforced = list(rows)
    assert unforced == [], (
        f"{len(unforced)} tables enable row-level security without forcing it, so the "
        f"owning application role bypasses every policy: {unforced[:5]}..."
    )


async def test_a_tenant_cannot_read_another_tenants_site(
    tenant_session_factory, seeded_tenants
) -> None:
    tenant_a, _tenant_b = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        hosts = (await session.execute(text("SELECT normalized_host FROM site"))).scalars()
        visible = sorted(hosts)
    assert visible == ["a.example"], f"tenant A saw {visible}"


async def test_an_unknown_tenant_scope_sees_nothing(
    tenant_session_factory, seeded_tenants
) -> None:
    """A scope owning no rows must see no rows, not every row."""
    async with tenant_session_factory(uuid4()) as session:
        count = (await session.execute(text("SELECT count(*) FROM site"))).scalar_one()
    assert count == 0, f"a tenant scope owning nothing saw {count} sites"


async def test_a_tenant_scope_cannot_write_a_row_labelled_for_another_tenant(
    tenant_session_factory, seeded_tenants
) -> None:
    """The WITH CHECK half of the policy must reject a mislabelled insert.

    Reading is only half of isolation. Without WITH CHECK a tenant could plant
    rows inside another tenant's scope even while unable to read them back.
    """
    tenant_a, tenant_b = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        with pytest.raises(DBAPIError) as exc:
            await session.execute(
                text(
                    "INSERT INTO site"
                    " (tenant_id, name, canonical_origin, normalized_host, mode, status)"
                    " VALUES (:tenant_id, 'planted', 'https://x.example', 'x.example',"
                    " 'observe', 'active')"
                ),
                {"tenant_id": tenant_b},
            )
    assert "row-level security" in str(exc.value).lower()


async def test_every_tenant_table_carries_a_non_null_tenant_id(raw_session) -> None:
    """A policy can only scope a table that records which tenant owns the row."""
    missing = []
    for table in TENANT_TABLES_SAMPLE:
        columns = (
            await raw_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = :t AND column_name = 'tenant_id'"
                    " AND is_nullable = 'NO'"
                ),
                {"t": table},
            )
        ).scalars()
        if not list(columns):
            missing.append(table)
    assert missing == [], f"tables without a non-null tenant_id: {missing}"
