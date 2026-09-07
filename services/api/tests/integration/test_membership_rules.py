"""The rules that decide who can change who is in a tenant.

Two of these are counting problems the database answers, not Python: whether a
tenant still has an owner after a change, and whether an address already has a
live invitation. A mocked session would let both pass while being wrong, so they
run against PostgreSQL as the NOBYPASSRLS application role.
"""

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role, TenantContext
from app.services.membership import MembershipService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

ISSUER = "https://issuer.example.com"


def context(tenant_id: UUID, actor_id: UUID, role: Role) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, actor_id=actor_id, role=role, trace_id="t")


@pytest_asyncio.fixture
async def tenant_with_members(engine):
    """A tenant with one owner and one editor, committed, then removed.

    Committed rather than staged because the last-owner rule is a count across
    rows, and a count inside one uncommitted transaction proves less than one
    across the table.
    """
    tenant_id = uuid4()
    owner_id, editor_id = uuid4(), uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenant (id, slug, name, status)"
                " VALUES (:id, :slug, 'membership', 'active')"
            ),
            {"id": tenant_id, "slug": f"membership-{tenant_id.hex[:8]}"},
        )
        for user_id, label, role in (
            (owner_id, "owner", "owner"),
            (editor_id, "editor", "editor"),
        ):
            await session.execute(
                text(
                    "INSERT INTO app_user"
                    " (id, issuer, subject, email, email_normalized, display_name, status)"
                    " VALUES (:id, :issuer, :subject, :email, :email, :label, 'active')"
                ),
                {
                    "id": user_id,
                    "issuer": ISSUER,
                    "subject": f"{label}-{user_id.hex[:8]}",
                    "email": f"{label}-{user_id.hex[:8]}@example.com",
                    "label": label,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO tenant_membership (tenant_id, user_id, role, status)"
                    " VALUES (:tenant_id, :user_id, :role, 'active')"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "role": role},
            )
    yield tenant_id, owner_id, editor_id
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
        await session.execute(
            text("DELETE FROM app_user WHERE id = ANY(:ids)"), {"ids": [owner_id, editor_id]}
        )
        await session.execute(text("DELETE FROM tenant WHERE id = :id"), {"id": tenant_id})


async def membership_id(session, tenant_id: UUID, user_id: UUID) -> UUID:
    return await session.scalar(
        text(
            "SELECT id FROM tenant_membership"
            " WHERE tenant_id = :tenant_id AND user_id = :user_id"
        ),
        {"tenant_id": tenant_id, "user_id": user_id},
    )


async def test_an_admin_cannot_create_an_owner(
    tenant_session_factory, tenant_with_members
) -> None:
    """Otherwise the two roles differ only until an admin invites themselves up."""
    tenant_id, _, editor_id = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        service = MembershipService(session, context(tenant_id, editor_id, Role.ADMIN))
        with pytest.raises(HTTPException) as raised:
            await service.invite("newcomer@example.com", Role.OWNER, "t")
    assert raised.value.status_code == 403
    assert raised.value.detail == "only_an_owner_grants_ownership"


async def test_an_admin_can_invite_below_ownership(
    tenant_session_factory, tenant_with_members
) -> None:
    tenant_id, _, editor_id = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        service = MembershipService(session, context(tenant_id, editor_id, Role.ADMIN))
        invitation = await service.invite("newcomer@example.com", Role.SEO_MANAGER, "t")
        assert invitation.role == "seo_manager"
        assert invitation.email_normalized == "newcomer@example.com"


async def test_the_last_owner_cannot_be_demoted_or_removed(
    tenant_session_factory, tenant_with_members
) -> None:
    """A tenant one request away from having nobody who can administer it."""
    tenant_id, owner_id, editor_id = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        owner_membership = await membership_id(session, tenant_id, owner_id)
        # An admin acting on the owner, so the refusal is the last-owner rule
        # rather than the self-edit rule.
        service = MembershipService(session, context(tenant_id, editor_id, Role.ADMIN))

        with pytest.raises(HTTPException) as demote:
            await service.change_role(owner_membership, Role.VIEWER, "t")
        assert demote.value.detail == "tenant_would_have_no_owner"

        with pytest.raises(HTTPException) as remove:
            await service.remove(owner_membership, "t")
        assert remove.value.detail == "tenant_would_have_no_owner"


async def test_a_second_owner_makes_the_first_removable(
    tenant_session_factory, tenant_with_members
) -> None:
    tenant_id, owner_id, editor_id = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        editor_membership = await membership_id(session, tenant_id, editor_id)
        owner_membership = await membership_id(session, tenant_id, owner_id)
        owner = MembershipService(session, context(tenant_id, owner_id, Role.OWNER))

        await owner.change_role(editor_membership, Role.OWNER, "t")
        await session.flush()

        promoted = MembershipService(session, context(tenant_id, editor_id, Role.OWNER))
        await promoted.remove(owner_membership, "t")
        await session.flush()

        status_now = await session.scalar(
            text("SELECT status FROM tenant_membership WHERE id = :id"),
            {"id": owner_membership},
        )
        # Suspended rather than deleted: proposals and audit rows still name
        # this user id, and deleting the row would make them unresolvable.
        assert status_now == "suspended"


async def test_nobody_edits_their_own_membership(
    tenant_session_factory, tenant_with_members
) -> None:
    tenant_id, owner_id, _ = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        own = await membership_id(session, tenant_id, owner_id)
        service = MembershipService(session, context(tenant_id, owner_id, Role.OWNER))
        with pytest.raises(HTTPException) as raised:
            await service.change_role(own, Role.VIEWER, "t")
    assert raised.value.detail == "cannot_change_own_membership"


async def test_inviting_someone_already_in_the_tenant_is_refused(
    tenant_session_factory, tenant_with_members
) -> None:
    tenant_id, owner_id, editor_id = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        existing = await session.scalar(
            text("SELECT email FROM app_user WHERE id = :id"), {"id": editor_id}
        )
        service = MembershipService(session, context(tenant_id, owner_id, Role.OWNER))
        with pytest.raises(HTTPException) as raised:
            await service.invite(existing, Role.ADMIN, "t")
    assert raised.value.detail == "already_a_member"


async def test_one_live_invitation_per_address(
    tenant_session_factory, tenant_with_members
) -> None:
    """Two open invitations would let two roles race to be the accepted one."""
    tenant_id, owner_id, _ = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        service = MembershipService(session, context(tenant_id, owner_id, Role.OWNER))
        await service.invite("twice@example.com", Role.VIEWER, "t")
        with pytest.raises(HTTPException) as raised:
            await service.invite("twice@example.com", Role.OWNER, "t")
        assert raised.value.detail == "invitation_already_open"

        # Revoking releases the address, because the uniqueness is over live rows.
        invitation = await session.scalar(
            text(
                "SELECT id FROM tenant_invitation"
                " WHERE tenant_id = :id AND email_normalized = 'twice@example.com'"
            ),
            {"id": tenant_id},
        )
        await service.revoke_invitation(invitation, "t")
        await session.flush()
        again = await service.invite("twice@example.com", Role.VIEWER, "t")
        assert again.id != invitation


async def test_a_member_from_another_tenant_is_not_found(
    tenant_session_factory, tenant_with_members
) -> None:
    tenant_id, owner_id, _ = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        service = MembershipService(session, context(tenant_id, owner_id, Role.OWNER))
        with pytest.raises(HTTPException) as raised:
            await service.change_role(uuid4(), Role.VIEWER, "t")
    assert raised.value.status_code == 404


async def test_a_viewer_cannot_change_membership_at_all(
    tenant_session_factory, tenant_with_members
) -> None:
    tenant_id, owner_id, editor_id = tenant_with_members
    async with tenant_session_factory(tenant_id) as session:
        service = MembershipService(session, context(tenant_id, editor_id, Role.VIEWER))
        with pytest.raises(HTTPException) as raised:
            await service.invite("nobody@example.com", Role.VIEWER, "t")
        assert raised.value.status_code == 403
        with pytest.raises(HTTPException):
            await service.remove(await membership_id(session, tenant_id, owner_id), "t")
