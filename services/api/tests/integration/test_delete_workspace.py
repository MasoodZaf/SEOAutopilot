"""Deleting a workspace on request, which the privacy policy promises.

Every foreign key to `tenant` is NO ACTION, so the order is read from the
catalog. These run against the real schema: a table added by a later migration
is covered, or the test that deletes a fully-populated tenant fails.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli.delete_workspace import DeletionOrderError, delete_workspace, deletion_order
from tests.conftest import requires_database


def test_children_are_emptied_before_their_parents() -> None:
    order = deletion_order(
        {"site", "page", "finding"},
        [("page", "site"), ("finding", "page"), ("page", "unrelated")],
    )
    assert order.index("finding") < order.index("page") < order.index("site")


def test_a_cycle_is_refused_rather_than_guessed() -> None:
    with pytest.raises(DeletionOrderError):
        deletion_order({"a", "b"}, [("a", "b"), ("b", "a")])


async def _seed(factory, tenant_id, slug):
    site_id, user_id = uuid4(), uuid4()
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant (id, slug, name, status) VALUES (:id, :slug, 'd', 'active')"),
            {"id": tenant_id, "slug": slug},
        )
        await session.execute(
            text(
                "INSERT INTO app_user (id, subject, issuer, email, email_normalized)"
                " VALUES (:id, :s, 'https://issuer.test', :e, :e)"
            ),
            {"id": user_id, "s": f"s-{user_id.hex[:8]}", "e": f"{user_id.hex[:8]}@t.test"},
        )
        await session.execute(
            text(
                "INSERT INTO tenant_membership (tenant_id, user_id, role, status)"
                " VALUES (:t, :u, 'owner', 'active')"
            ),
            {"t": tenant_id, "u": user_id},
        )
        await session.execute(
            text(
                "INSERT INTO site (id, tenant_id, name, canonical_origin, normalized_host,"
                " status, verified_at) VALUES (:id, :t, 'd', 'https://d.test', :h, 'active', now())"
            ),
            {"id": site_id, "t": tenant_id, "h": f"{site_id.hex[:8]}.test"},
        )
        await session.execute(
            text(
                "INSERT INTO connector (id, tenant_id, site_id, type, status)"
                " VALUES (:id, :t, :s, 'google_analytics', 'active')"
            ),
            {"id": uuid4(), "t": tenant_id, "s": site_id},
        )
    return user_id


async def _count(factory, tenant_id):
    async with factory() as session:
        return {
            table: await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {column} = :id"), {"id": tenant_id}
            )
            for table, column in (
                ("tenant", "id"),
                ("tenant_membership", "tenant_id"),
                ("site", "tenant_id"),
                ("connector", "tenant_id"),
            )
        }


@requires_database
@pytest.mark.asyncio
async def test_one_workspace_goes_and_its_neighbour_stays(database_url, engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    doomed, neighbour = uuid4(), uuid4()
    doomed_user = await _seed(factory, doomed, f"doomed-{doomed.hex[:8]}")
    neighbour_user = await _seed(factory, neighbour, f"keep-{neighbour.hex[:8]}")

    # The wrong slug is refused before anything is touched.
    with pytest.raises(SystemExit):
        await delete_workspace(database_url, doomed, "keep", execute=True)

    # A dry run reports what --execute would remove and removes nothing.
    counts, orphaned = await delete_workspace(
        database_url, doomed, f"doomed-{doomed.hex[:8]}", execute=False
    )
    assert counts["site"] == 1 and counts["connector"] == 1 and counts["tenant"] == 1
    assert orphaned == [doomed_user]
    assert (await _count(factory, doomed))["site"] == 1

    await delete_workspace(database_url, doomed, f"doomed-{doomed.hex[:8]}", execute=True)
    assert set((await _count(factory, doomed)).values()) == {0}
    assert set((await _count(factory, neighbour)).values()) == {1}

    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM connector WHERE tenant_id = :id"), {"id": neighbour})
        await session.execute(text("DELETE FROM site WHERE tenant_id = :id"), {"id": neighbour})
        await session.execute(text("DELETE FROM tenant_membership WHERE tenant_id = :id"), {"id": neighbour})
        await session.execute(text("DELETE FROM tenant WHERE id = :id"), {"id": neighbour})
        await session.execute(
            text("DELETE FROM app_user WHERE id IN (:a, :b)"), {"a": doomed_user, "b": neighbour_user}
        )
