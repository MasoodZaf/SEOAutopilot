"""The one invitation with no member behind it.

Every membership comes from an invitation and every invitation comes from a
member, so the first owner has nowhere to come from. This closes that loop
without opening an HTTP route that mints owners.
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli.bootstrap_owner import BootstrapError, bootstrap_owner
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


@pytest_asyncio.fixture
async def empty_tenant(engine, database_url):
    tenant_id, slug = uuid4(), None
    factory = async_sessionmaker(engine, expire_on_commit=False)
    slug = f"bootstrap-{tenant_id.hex[:8]}"
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenant (id, slug, name, status)"
                " VALUES (:id, :slug, 'bootstrap', 'active')"
            ),
            {"id": tenant_id, "slug": slug},
        )
    yield tenant_id, slug, database_url
    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM audit_event WHERE tenant_id = :id"), {"id": tenant_id}
        )
        await session.execute(
            text("DELETE FROM tenant_invitation WHERE tenant_id = :id"), {"id": tenant_id}
        )
        await session.execute(
            text("DELETE FROM tenant_membership WHERE tenant_id = :id"), {"id": tenant_id}
        )
        await session.execute(text("DELETE FROM tenant WHERE id = :id"), {"id": tenant_id})


async def test_it_invites_the_first_owner_and_records_who_did(engine, empty_tenant) -> None:
    tenant_id, slug, url = empty_tenant

    message = await bootstrap_owner(url, slug, "  Ada@Example.COM ")
    assert "ada@example.com" in message

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        row = (
            await session.execute(
                text(
                    "SELECT email_normalized, role, invited_by FROM tenant_invitation"
                    " WHERE tenant_id = :id"
                ),
                {"id": tenant_id},
            )
        ).one()
        assert row == ("ada@example.com", "owner", None)
        actor = (
            await session.execute(
                text(
                    "SELECT actor_type, action FROM audit_event"
                    " WHERE tenant_id = :id AND action = 'membership.bootstrapped'"
                ),
                {"id": tenant_id},
            )
        ).one()
        assert actor == ("operator", "membership.bootstrapped")


async def test_it_refuses_a_tenant_that_already_has_an_owner(engine, empty_tenant) -> None:
    """A bootstrap, not a standing route to ownership.

    An operator with a database credential can do anything; the point is that
    doing it this way has to be a deliberate decision made in psql, where it is
    visible, rather than a command that quietly mints owners.
    """
    tenant_id, slug, url = empty_tenant
    user_id = uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO app_user (id, issuer, subject, email, email_normalized, status)"
                " VALUES (:id, 'https://issuer.example.com', :subject, :email, :email, 'active')"
            ),
            {"id": user_id, "subject": f"s-{user_id.hex[:8]}", "email": f"o-{user_id.hex[:8]}@x.com"},
        )
        await session.execute(
            text(
                "INSERT INTO tenant_membership (tenant_id, user_id, role, status)"
                " VALUES (:tenant_id, :user_id, 'owner', 'active')"
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        )

    with pytest.raises(BootstrapError, match="already has 1 active owner"):
        await bootstrap_owner(url, slug, "second@example.com")

    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM tenant_membership WHERE tenant_id = :id"), {"id": tenant_id}
        )
        await session.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})


async def test_it_refuses_an_unknown_tenant_and_a_repeat_invitation(empty_tenant) -> None:
    _, slug, url = empty_tenant
    with pytest.raises(BootstrapError, match="no tenant with slug"):
        await bootstrap_owner(url, "no-such-tenant", "ada@example.com")

    await bootstrap_owner(url, slug, "ada@example.com")
    with pytest.raises(BootstrapError, match="already has an open invitation"):
        await bootstrap_owner(url, slug, "ada@example.com")
