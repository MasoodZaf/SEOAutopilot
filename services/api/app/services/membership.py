"""Who is in a tenant, and who is allowed to change that.

Three rules here are worth more than the endpoints they sit behind.

An admin cannot create an owner. Otherwise the distinction between the two
roles lasts exactly as long as it takes an admin to invite themselves back at a
higher grade, and the role hierarchy stops meaning anything.

A tenant cannot lose its last owner. Removing or demoting one is otherwise a
single request away from a tenant nobody can administer, with no path back that
does not involve an operator on the host.

Nobody changes their own membership. Self-demotion is only ever a mistake, and
self-promotion is the escalation the first rule exists to stop, reached by
another route.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Role, TenantContext
from app.db.models import AppUser, AuditEvent, TenantInvitation, TenantMembership
from app.services.sites import stable_hash

INVITATION_LIFETIME = timedelta(days=7)

# Roles that may change who is in a tenant at all.
GRANTING_ROLES = (Role.OWNER, Role.ADMIN)


class MembershipService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    # -- reading ---------------------------------------------------------

    async def list_members(self) -> list[tuple[TenantMembership, AppUser]]:
        rows = await self.session.execute(
            select(TenantMembership, AppUser)
            .join(AppUser, AppUser.id == TenantMembership.user_id)
            .where(TenantMembership.tenant_id == self.context.tenant_id)
            .order_by(TenantMembership.created_at)
        )
        return [(membership, user) for membership, user in rows.all()]

    async def list_invitations(self) -> list[TenantInvitation]:
        rows = await self.session.scalars(
            select(TenantInvitation)
            .where(
                TenantInvitation.tenant_id == self.context.tenant_id,
                TenantInvitation.accepted_at.is_(None),
                TenantInvitation.revoked_at.is_(None),
            )
            .order_by(TenantInvitation.created_at.desc())
        )
        return list(rows.all())

    # -- changing --------------------------------------------------------

    async def invite(self, email: str, role: Role, trace_id: str) -> TenantInvitation:
        self.context.require(*GRANTING_ROLES)
        self._may_grant(role)
        normalized = email.strip().lower()
        if "@" not in normalized or len(normalized) < 3:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="email_invalid"
            )
        already = await self.session.scalar(
            select(TenantMembership)
            .join(AppUser, AppUser.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == self.context.tenant_id,
                AppUser.email_normalized == normalized,
            )
        )
        if already is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="already_a_member"
            )
        invitation = TenantInvitation(
            tenant_id=self.context.tenant_id,
            email_normalized=normalized,
            role=role.value,
            invited_by=self.context.actor_id,
            expires_at=datetime.now(UTC) + INVITATION_LIFETIME,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(invitation)
                await self.session.flush()
        except IntegrityError as error:
            # One live invitation per address per tenant. A second would let two
            # roles race to be the one that is accepted.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="invitation_already_open"
            ) from error
        self._audit(
            "membership.invited",
            "tenant_invitation",
            invitation.id,
            {"email": normalized, "role": role.value},
            trace_id,
        )
        return invitation

    async def revoke_invitation(self, invitation_id: UUID, trace_id: str) -> None:
        self.context.require(*GRANTING_ROLES)
        invitation = await self.session.scalar(
            select(TenantInvitation).where(
                TenantInvitation.id == invitation_id,
                TenantInvitation.tenant_id == self.context.tenant_id,
            )
        )
        if invitation is None or invitation.accepted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="invitation_not_found"
            )
        if invitation.revoked_at is not None:
            return
        invitation.revoked_at = datetime.now(UTC)
        self._audit(
            "membership.invitation_revoked",
            "tenant_invitation",
            invitation.id,
            {"email": invitation.email_normalized},
            trace_id,
        )

    async def change_role(self, membership_id: UUID, role: Role, trace_id: str) -> TenantMembership:
        self.context.require(*GRANTING_ROLES)
        self._may_grant(role)
        membership = await self._membership(membership_id)
        self._refuse_self(membership)
        if membership.role == Role.OWNER.value and role != Role.OWNER:
            await self._refuse_last_owner(membership)
        previous = membership.role
        membership.role = role.value
        self._audit(
            "membership.role_changed",
            "tenant_membership",
            membership.id,
            {"from": previous, "to": role.value, "user_id": str(membership.user_id)},
            trace_id,
        )
        return membership

    async def remove(self, membership_id: UUID, trace_id: str) -> None:
        self.context.require(*GRANTING_ROLES)
        membership = await self._membership(membership_id)
        self._refuse_self(membership)
        if membership.role == Role.OWNER.value:
            await self._refuse_last_owner(membership)
        # Suspended rather than deleted. The audit trail and every proposal this
        # person authored still name their user id, and a removal that erased
        # the row would make those unresolvable.
        membership.status = "suspended"
        self._audit(
            "membership.removed",
            "tenant_membership",
            membership.id,
            {"user_id": str(membership.user_id), "role": membership.role},
            trace_id,
        )

    # -- rules -----------------------------------------------------------

    def _may_grant(self, role: Role) -> None:
        if role == Role.OWNER and self.context.role != Role.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="only_an_owner_grants_ownership"
            )

    def _refuse_self(self, membership: TenantMembership) -> None:
        if membership.user_id == self.context.actor_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="cannot_change_own_membership"
            )

    async def _refuse_last_owner(self, membership: TenantMembership) -> None:
        remaining = await self.session.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(
                TenantMembership.tenant_id == self.context.tenant_id,
                TenantMembership.role == Role.OWNER.value,
                TenantMembership.status == "active",
                TenantMembership.id != membership.id,
            )
        )
        if not remaining:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="tenant_would_have_no_owner"
            )

    async def _membership(self, membership_id: UUID) -> TenantMembership:
        membership = await self.session.scalar(
            select(TenantMembership).where(
                TenantMembership.id == membership_id,
                TenantMembership.tenant_id == self.context.tenant_id,
                TenantMembership.status == "active",
            )
        )
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="membership_not_found"
            )
        return membership

    def _audit(
        self,
        action: str,
        resource_type: str,
        resource_id: UUID,
        metadata: dict[str, object],
        trace_id: str,
    ) -> None:
        payload = {**metadata, "actor_id": str(self.context.actor_id)}
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                trace_id=trace_id,
                metadata_json=payload,
                event_hash=stable_hash(payload),
            )
        )
