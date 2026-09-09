"""Turning a verified identity into authority over one tenant's data.

A verified OIDC token establishes a person. It establishes nothing about whose
data they may see: that is `tenant_membership`, and the only way one comes to
exist is that somebody who already had authority invited the address.

The order below matters. Membership is read after the user is resolved and
never inferred from the token, an invitation is matched only on an address the
provider marked verified, and the tenant a request acts on must be one the
caller provably belongs to. A header may *select* among those tenants; it can
never widen the set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import ActorContext, Role, TenantContext
from app.core.oidc import VerifiedIdentity
from app.db.models import AppUser, TenantInvitation, TenantMembership


class IdentityResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_actor(
        self, identity: VerifiedIdentity, *, trace_id: str
    ) -> tuple[ActorContext, list[TenantMembership]]:
        """Establish the person, and report what they are in without demanding it.

        Everything `resolve` does up to the point where it insists on a
        membership. Signup and the tenant chooser need exactly that much: a
        proven person, their invitations already accepted, and an honest list
        that may be empty.
        """
        now = datetime.now(UTC)
        user = await self._user(identity, now)
        if user.status != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_suspended")
        await self._accept_open_invitations(user, identity, now)
        memberships = await self._memberships(user)
        user.last_seen_at = now
        actor = ActorContext(
            actor_id=user.id,
            email=user.email,
            display_name=user.display_name,
            trace_id=trace_id,
        )
        return actor, memberships

    async def resolve(
        self, identity: VerifiedIdentity, *, requested_tenant_id: UUID | None, trace_id: str
    ) -> TenantContext:
        now = datetime.now(UTC)
        user = await self._user(identity, now)
        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="account_suspended"
            )
        await self._accept_open_invitations(user, identity, now)
        memberships = await self._memberships(user)
        if not memberships:
            # Authenticated and entitled to nothing. This is the ordinary state
            # of a stranger with a valid Google account, so it is a 403 with a
            # reason rather than a 401 that invites them to log in again.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="no_tenant_membership"
            )
        membership = self._select(memberships, requested_tenant_id)
        user.last_seen_at = now
        return TenantContext(
            tenant_id=membership.tenant_id,
            actor_id=user.id,
            role=_role(membership.role),
            trace_id=trace_id,
        )

    async def _user(self, identity: VerifiedIdentity, now: datetime) -> AppUser:
        existing = await self.session.scalar(
            select(AppUser).where(
                AppUser.issuer == identity.issuer, AppUser.subject == identity.subject
            )
        )
        if existing is not None:
            # The provider owns the label; keep it current so an operator
            # inviting or removing people is looking at today's address.
            existing.email = identity.email
            existing.email_normalized = identity.email_normalized
            if identity.display_name:
                existing.display_name = identity.display_name
            return existing

        if not identity.email_verified:
            # A first login has nothing to match an invitation against except
            # the address, and an unverified address is a claim by the person
            # holding the account, not a fact established by the provider.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="email_not_verified"
            )
        user = AppUser(
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            email_normalized=identity.email_normalized,
            display_name=identity.display_name,
            status="active",
            created_at=now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(user)
                await self.session.flush()
        except IntegrityError as error:
            # The address already belongs to a different provider subject. That
            # is either the same human with a second account or an address that
            # was reassigned, and this service cannot tell which. Refusing is
            # the only safe answer: silently relinking would move one person's
            # memberships onto whoever holds the address now.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="email_already_linked"
            ) from error
        return user

    async def _accept_open_invitations(
        self, user: AppUser, identity: VerifiedIdentity, now: datetime
    ) -> None:
        """Turn every live invitation for this address into a membership.

        Run on every request, not only a first login, so somebody already using
        one tenant who is invited into a second is in it on their next call
        rather than after signing out. An address the provider has not verified
        matches nothing.
        """
        if not identity.email_verified:
            return
        invitations = (
            await self.session.scalars(
                select(TenantInvitation).where(
                    TenantInvitation.email_normalized == identity.email_normalized,
                    TenantInvitation.accepted_at.is_(None),
                    TenantInvitation.revoked_at.is_(None),
                    TenantInvitation.expires_at > now,
                )
            )
        ).all()
        for invitation in invitations:
            existing = await self.session.scalar(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == invitation.tenant_id,
                    TenantMembership.user_id == user.id,
                )
            )
            if existing is None:
                self.session.add(
                    TenantMembership(
                        tenant_id=invitation.tenant_id,
                        user_id=user.id,
                        role=invitation.role,
                        status="active",
                        invited_by=invitation.invited_by,
                    )
                )
            invitation.accepted_at = now
            invitation.accepted_user_id = user.id
        if invitations:
            await self.session.flush()

    async def _memberships(self, user: AppUser) -> list[TenantMembership]:
        rows = await self.session.scalars(
            select(TenantMembership)
            .where(
                TenantMembership.user_id == user.id,
                TenantMembership.status == "active",
            )
            .order_by(TenantMembership.created_at)
        )
        return list(rows.all())

    @staticmethod
    def _select(
        memberships: list[TenantMembership], requested_tenant_id: UUID | None
    ) -> TenantMembership:
        if requested_tenant_id is not None:
            for membership in memberships:
                if membership.tenant_id == requested_tenant_id:
                    return membership
            # Naming the tenant does not grant it. A caller who asks for one
            # they are not in is told no, in the same words whether or not the
            # tenant exists.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="tenant_not_permitted"
            )
        if len(memberships) == 1:
            return memberships[0]
        # Picking one for them would silently act on the wrong tenant's data.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="tenant_selection_required"
        )


def _role(value: str) -> Role:
    try:
        return Role(value)
    except ValueError as error:
        # A membership carrying a role this build does not know is not a viewer
        # by default. It is a row this build cannot interpret.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="membership_role_unknown"
        ) from error
