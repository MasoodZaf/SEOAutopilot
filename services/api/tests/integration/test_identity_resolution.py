"""Turning a verified token into authority, proved against PostgreSQL.

Every service test in this repository mocks the session, which is how a policy
that is enabled but inert reads exactly like one that works. Authentication is
the last place that blind spot is acceptable: the rules here are what stand
between a stranger with a valid Google account and somebody else's tenant, and
three of them are enforced by the database rather than by Python.

So these run as `seo_autopilot_app` -- the NOSUPERUSER NOBYPASSRLS role the
services use -- through the same `app.authenticating` window the auth dependency
opens.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role
from app.core.oidc import VerifiedIdentity
from app.services.identity import IdentityResolver
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

ISSUER = "https://issuer.example.com"


def identity(
    subject: str = "subject-1",
    email: str = "ada@example.com",
    *,
    verified: bool = True,
) -> VerifiedIdentity:
    return VerifiedIdentity(
        issuer=ISSUER,
        subject=subject,
        email=email,
        email_verified=verified,
        display_name="Ada",
    )


@pytest.fixture
def authenticating(app_engine):
    """The narrow window the auth dependency opens, on the application role."""
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    class _Window:
        async def __aenter__(self):
            self._session = factory()
            await self._session.__aenter__()
            self._transaction = self._session.begin()
            await self._transaction.__aenter__()
            await self._session.execute(
                text("SELECT set_config('app.authenticating', 'on', true)")
            )
            return self._session

        async def __aexit__(self, *exc) -> None:
            await self._session.rollback()
            await self._session.__aexit__(None, None, None)

    return _Window


@pytest_asyncio.fixture
async def seeded(engine):
    """Two committed tenants and one inviting user, removed afterwards."""
    tenant_a, tenant_b, inviter = uuid4(), uuid4(), uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        for tenant_id, slug in ((tenant_a, "identity-a"), (tenant_b, "identity-b")):
            await session.execute(
                text(
                    "INSERT INTO tenant (id, slug, name, status)"
                    " VALUES (:id, :slug, :slug, 'active')"
                ),
                {"id": tenant_id, "slug": f"{slug}-{tenant_id.hex[:8]}"},
            )
        await session.execute(
            text(
                "INSERT INTO app_user (id, issuer, subject, email, email_normalized, status)"
                " VALUES (:id, :issuer, :subject, :email, :email, 'active')"
            ),
            {
                "id": inviter,
                "issuer": ISSUER,
                "subject": f"inviter-{inviter.hex[:8]}",
                "email": f"inviter-{inviter.hex[:8]}@example.com",
            },
        )
    yield tenant_a, tenant_b, inviter
    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM tenant_membership WHERE tenant_id = ANY(:ids)"),
            {"ids": [tenant_a, tenant_b]},
        )
        await session.execute(
            text("DELETE FROM tenant_invitation WHERE tenant_id = ANY(:ids)"),
            {"ids": [tenant_a, tenant_b]},
        )
        await session.execute(
            text("DELETE FROM app_user WHERE issuer = :issuer"), {"issuer": ISSUER}
        )
        await session.execute(
            text("DELETE FROM tenant WHERE id = ANY(:ids)"), {"ids": [tenant_a, tenant_b]}
        )


async def invite(
    session, tenant_id: UUID, inviter: UUID, email: str, role: str = "editor", **overrides
) -> None:
    values = {
        "tenant_id": tenant_id,
        "email": email,
        "role": role,
        "invited_by": inviter,
        "expires_at": datetime.now(UTC) + timedelta(days=7),
        "revoked_at": None,
    }
    values.update(overrides)
    await session.execute(
        text(
            "INSERT INTO tenant_invitation"
            " (tenant_id, email_normalized, role, invited_by, expires_at, revoked_at)"
            " VALUES (:tenant_id, :email, :role, :invited_by, :expires_at, :revoked_at)"
        ),
        values,
    )


async def test_a_valid_token_with_no_invitation_is_entitled_to_nothing(
    authenticating, seeded
) -> None:
    """The ordinary state of a stranger holding a real Google account.

    A 401 would invite them to sign in again to fix something signing in cannot
    fix, so this is a 403 that says what is missing.
    """
    async with authenticating() as session:
        with pytest.raises(HTTPException) as raised:
            await IdentityResolver(session).resolve(
                identity(), requested_tenant_id=None, trace_id="t"
            )
    assert raised.value.status_code == 403
    assert raised.value.detail == "no_tenant_membership"


async def test_an_invitation_is_what_creates_the_membership(authenticating, seeded) -> None:
    tenant_a, _, inviter = seeded
    async with authenticating() as session:
        await invite(session, tenant_a, inviter, "ada@example.com", role="seo_manager")

        context = await IdentityResolver(session).resolve(
            identity(), requested_tenant_id=None, trace_id="trace-1"
        )

        assert context.tenant_id == tenant_a
        assert context.role == Role.SEO_MANAGER
        assert context.trace_id == "trace-1"
        accepted = (
            await session.execute(
                text(
                    "SELECT accepted_at IS NOT NULL, accepted_user_id"
                    " FROM tenant_invitation WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_a},
            )
        ).one()
        assert accepted[0] is True
        assert accepted[1] == context.actor_id


async def test_a_second_call_reuses_the_identity_and_adds_no_second_membership(
    authenticating, seeded
) -> None:
    tenant_a, _, inviter = seeded
    async with authenticating() as session:
        await invite(session, tenant_a, inviter, "ada@example.com")
        resolver = IdentityResolver(session)
        first = await resolver.resolve(identity(), requested_tenant_id=None, trace_id="t")
        second = await resolver.resolve(identity(), requested_tenant_id=None, trace_id="t")

        assert first.actor_id == second.actor_id
        count = await session.scalar(
            text("SELECT count(*) FROM tenant_membership WHERE user_id = :user_id"),
            {"user_id": first.actor_id},
        )
        assert count == 1


async def test_an_expired_or_revoked_invitation_grants_nothing(authenticating, seeded) -> None:
    tenant_a, tenant_b, inviter = seeded
    async with authenticating() as session:
        await invite(
            session,
            tenant_a,
            inviter,
            "ada@example.com",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        await invite(
            session,
            tenant_b,
            inviter,
            "ada@example.com",
            revoked_at=datetime.now(UTC) - timedelta(hours=1),
        )
        with pytest.raises(HTTPException) as raised:
            await IdentityResolver(session).resolve(
                identity(), requested_tenant_id=None, trace_id="t"
            )
    assert raised.value.detail == "no_tenant_membership"


async def test_a_first_login_on_an_unverified_address_matches_no_invitation(
    authenticating, seeded
) -> None:
    """The address is the only thing an invitation can be matched on.

    An unverified one is a claim by whoever holds the account, so accepting it
    would let anyone who can type an invited colleague's address into a fresh
    provider account walk into that tenant.
    """
    tenant_a, _, inviter = seeded
    async with authenticating() as session:
        await invite(session, tenant_a, inviter, "ada@example.com")
        with pytest.raises(HTTPException) as raised:
            await IdentityResolver(session).resolve(
                identity(verified=False), requested_tenant_id=None, trace_id="t"
            )
    assert raised.value.detail == "email_not_verified"


async def test_two_memberships_need_the_caller_to_say_which_tenant(
    authenticating, seeded
) -> None:
    tenant_a, tenant_b, inviter = seeded
    async with authenticating() as session:
        await invite(session, tenant_a, inviter, "ada@example.com", role="viewer")
        await invite(session, tenant_b, inviter, "ada@example.com", role="owner")
        resolver = IdentityResolver(session)

        with pytest.raises(HTTPException) as raised:
            await resolver.resolve(identity(), requested_tenant_id=None, trace_id="t")
        assert raised.value.status_code == 409
        assert raised.value.detail == "tenant_selection_required"

        chosen = await resolver.resolve(identity(), requested_tenant_id=tenant_b, trace_id="t")
        assert chosen.tenant_id == tenant_b
        assert chosen.role == Role.OWNER


async def test_naming_a_tenant_does_not_join_it(authenticating, seeded) -> None:
    """The header selects among memberships; it never creates one."""
    tenant_a, tenant_b, inviter = seeded
    async with authenticating() as session:
        await invite(session, tenant_a, inviter, "ada@example.com")
        with pytest.raises(HTTPException) as raised:
            await IdentityResolver(session).resolve(
                identity(), requested_tenant_id=tenant_b, trace_id="t"
            )
    assert raised.value.status_code == 403
    assert raised.value.detail == "tenant_not_permitted"


async def test_a_suspended_membership_is_not_a_membership(authenticating, seeded) -> None:
    tenant_a, _, inviter = seeded
    async with authenticating() as session:
        await invite(session, tenant_a, inviter, "ada@example.com")
        resolver = IdentityResolver(session)
        context = await resolver.resolve(identity(), requested_tenant_id=None, trace_id="t")
        await session.execute(
            text("UPDATE tenant_membership SET status='suspended' WHERE user_id = :user_id"),
            {"user_id": context.actor_id},
        )
        with pytest.raises(HTTPException) as raised:
            await resolver.resolve(identity(), requested_tenant_id=None, trace_id="t")
    assert raised.value.detail == "no_tenant_membership"


async def test_an_address_already_linked_to_another_subject_is_refused(
    authenticating, seeded
) -> None:
    """Two provider accounts, one address, and no way to tell which is the person.

    Relinking silently would move one person's memberships onto whoever holds
    the address now, which is exactly what an address reassignment looks like.
    """
    tenant_a, _, inviter = seeded
    async with authenticating() as session:
        await invite(session, tenant_a, inviter, "ada@example.com")
        resolver = IdentityResolver(session)
        await resolver.resolve(identity(), requested_tenant_id=None, trace_id="t")

        with pytest.raises(HTTPException) as raised:
            await resolver.resolve(
                identity(subject="a-different-provider-account"),
                requested_tenant_id=None,
                trace_id="t",
            )
    assert raised.value.status_code == 409
    assert raised.value.detail == "email_already_linked"


async def test_the_authenticating_window_opens_three_tables_and_no_others(
    authenticating, seeded
) -> None:
    """The exemption has to be narrow or it is just an unscoped connection.

    `app.authenticating` is set on a session that resolves a membership and is
    then closed. If it also opened the tenant tables, every request would begin
    with a connection that could read anything.
    """
    async with authenticating() as session:
        for table in ("app_user", "tenant_membership", "tenant_invitation"):
            await session.execute(text(f"SELECT count(*) FROM {table}"))

        for closed in ("site", "proposal", "connector", "search_metric", "audit_event"):
            visible = await session.scalar(text(f"SELECT count(*) FROM {closed}"))
            assert visible == 0, (
                f"the authenticating session can read {closed}; the exemption is supposed "
                "to cover identity only"
            )
