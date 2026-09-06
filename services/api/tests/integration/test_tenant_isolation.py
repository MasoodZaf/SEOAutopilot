"""Row-level security, proved against PostgreSQL rather than a mock.

`AGENTS.md` makes tenant isolation the primary boundary and `get_tenant_session`
sets `app.tenant_id` so row-level security can enforce it. Whether the database
actually enforces it has never been tested: every service test mocks the session,
so a policy that is enabled but inert reads exactly like one that works.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

TENANT_TABLES_SAMPLE = ["site", "proposal", "proposal_approval", "audit_event", "connector"]

# All 50 tenant tables enable row-level security and every policy is written
# correctly, but none of them is in effect. There are two independent reasons,
# and only fixing both restores isolation:
#
#   1. No table sets FORCE ROW LEVEL SECURITY, and the application connects as
#      the role that ran the migrations, so it owns every table. An owner
#      bypasses its own policies unless they are forced.
#   2. More fundamentally, that role is a SUPERUSER with BYPASSRLS -- the
#      postgres image makes POSTGRES_USER a superuser. A superuser ignores row
#      security unconditionally, and FORCE does not apply to it. Forcing alone
#      was measured to change nothing.
#
# Isolation today therefore rests entirely on the explicit `tenant_id` filters
# in the service layer. Any query that omits one -- and some rely on RLS rather
# than filtering -- has no isolation at all.
#
# The remedy is verified: a NOSUPERUSER NOBYPASSRLS application role, granted
# only DML, with FORCE enabled for defence in depth. Under it a tenant sees only
# its own rows, an unknown scope and an unset scope both see nothing, and a
# cross-tenant insert is rejected by the WITH CHECK half of the policy.
#
# It is not a single migration, which is why this is tracked rather than fixed
# here: the new role also stops the OAuth callback's deliberately unscoped state
# lookup and the twelve worker modules that never set the tenant GUC, two of
# which -- the outbox relay and the routine scheduler -- are cross-tenant by
# design and need their own BYPASSRLS role. Hardening track H1 in ROADMAP.md.
#
# `strict=True` is the point: when RLS is forced these turn from expected
# failures into unexpected passes, and the suite goes red until the markers
# come off. The gap cannot be quietly left half-fixed.
rls_not_yet_enforced = pytest.mark.xfail(
    strict=True,
    reason="H1: the application role is a superuser, so every row-security policy is inert",
)


@rls_not_yet_enforced
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


@rls_not_yet_enforced
async def test_a_tenant_cannot_read_another_tenants_site(
    tenant_session_factory, seeded_tenants
) -> None:
    tenant_a, _tenant_b = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        hosts = (await session.execute(text("SELECT normalized_host FROM site"))).scalars()
        visible = sorted(hosts)
    assert visible == ["a.example"], f"tenant A saw {visible}"


@rls_not_yet_enforced
async def test_an_unknown_tenant_scope_sees_nothing(
    tenant_session_factory, seeded_tenants
) -> None:
    """A scope owning no rows must see no rows, not every row."""
    async with tenant_session_factory(uuid4()) as session:
        count = (await session.execute(text("SELECT count(*) FROM site"))).scalar_one()
    assert count == 0, f"a tenant scope owning nothing saw {count} sites"


@pytest.mark.parametrize("table", TENANT_TABLES_SAMPLE)
async def test_a_tenant_scope_cannot_write_rows_for_another_tenant(
    tenant_session_factory, seeded_tenants, table: str
) -> None:
    """The WITH CHECK half of each policy must reject a mislabelled insert."""
    tenant_a, _tenant_b = seeded_tenants
    async with tenant_session_factory(tenant_a) as session:
        columns = (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = :t AND is_nullable = 'NO' AND column_default IS NULL"
                ),
                {"t": table},
            )
        ).scalars()
        required = set(columns)
        assert "tenant_id" in required, f"{table} has no non-null tenant_id"
