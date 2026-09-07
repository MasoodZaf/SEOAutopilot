"""Managing who is in a tenant.

Every route here is tenant scoped by the session, so a member id from another
tenant is a 404 rather than a leak. The rules that matter -- an admin cannot
create an owner, a tenant cannot lose its last one, nobody edits their own
membership -- live in the service, because they are properties of the change and
not of the transport.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.api.schemas import (
    InvitationCollection,
    InvitationCreate,
    InvitationEnvelope,
    InvitationRead,
    MemberCollection,
    MemberEnvelope,
    MemberRead,
    MemberRoleUpdate,
)
from app.core.auth import TenantContextDependency
from app.core.context import Role
from app.db.models import AppUser, TenantMembership
from app.db.session import TenantSession
from app.services.membership import MembershipService

router = APIRouter(prefix="/v1/members", tags=["members"])


def _member(membership: TenantMembership, user: AppUser) -> MemberRead:
    return MemberRead(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        status=membership.status,
        created_at=membership.created_at,
    )


@router.get("", response_model=MemberCollection)
async def list_members(
    context: TenantContextDependency, session: TenantSession
) -> MemberCollection:
    rows = await MembershipService(session, context).list_members()
    return MemberCollection(
        data=[_member(membership, user) for membership, user in rows],
        meta={"trace_id": context.trace_id, "count": len(rows)},
    )


@router.get("/invitations", response_model=InvitationCollection)
async def list_invitations(
    context: TenantContextDependency, session: TenantSession
) -> InvitationCollection:
    invitations = await MembershipService(session, context).list_invitations()
    return InvitationCollection(
        data=[InvitationRead.model_validate(item) for item in invitations],
        meta={"trace_id": context.trace_id, "count": len(invitations)},
    )


@router.post(
    "/invitations", response_model=InvitationEnvelope, status_code=status.HTTP_201_CREATED
)
async def invite_member(
    command: InvitationCreate, context: TenantContextDependency, session: TenantSession
) -> InvitationEnvelope:
    invitation = await MembershipService(session, context).invite(
        command.email, Role(command.role.value), context.trace_id
    )
    return InvitationEnvelope(
        data=InvitationRead.model_validate(invitation),
        meta={"trace_id": context.trace_id},
    )


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: UUID, context: TenantContextDependency, session: TenantSession
) -> Response:
    await MembershipService(session, context).revoke_invitation(
        invitation_id, context.trace_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{membership_id}", response_model=MemberEnvelope)
async def change_member_role(
    membership_id: UUID,
    command: MemberRoleUpdate,
    context: TenantContextDependency,
    session: TenantSession,
) -> MemberEnvelope:
    membership = await MembershipService(session, context).change_role(
        membership_id, Role(command.role.value), context.trace_id
    )
    # The membership was read under the tenant scope, so its user is one this
    # tenant may see; the row-level policy on app_user says the same.
    user = await session.get(AppUser, membership.user_id)
    if user is None:  # pragma: no cover - the foreign key makes this unreachable
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="membership_not_found")
    return MemberEnvelope(
        data=_member(membership, user), meta={"trace_id": context.trace_id}
    )


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    membership_id: UUID, context: TenantContextDependency, session: TenantSession
) -> Response:
    await MembershipService(session, context).remove(membership_id, context.trace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
