"""Creating a workspace for somebody who is not in one yet.

This is the one route that hands out authority nobody granted, so it is worth
being precise about why that is safe here and was not safe for invitations.

An invitation grants access to data that already exists and belongs to someone
else, which is why it can only come from a member. This creates a tenant that
contains nothing, and makes its creator the owner of an empty room. The person
is already proven -- a verified OIDC token with an email the provider marked
verified -- so what they get is a workspace of their own and no reach whatsoever
into anybody else's.

Two limits keep that from being a way to make the database grow for free: a cap
on how many tenants one person may own, and a slug derived from the name they
chose rather than one they supply, so tenant slugs cannot be squatted.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import ActorContext, Role
from app.db.models import AppUser, AuditEvent, Tenant, TenantMembership
from app.services.sites import stable_hash

# How many workspaces one person may own. High enough that nobody legitimate
# meets it -- an agency running several clients' sites separately is the case
# this is sized for -- and low enough that a script cannot mint thousands.
MAX_OWNED_TENANTS = 5

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """A URL-safe stem for a tenant name.

    Derived, never accepted from the request. A caller who could choose their
    own slug could take `codearc-pilot` before we did, and slugs appear in
    operator commands where taking the wrong one is a real mistake.
    """
    stem = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    # Truncated well short of the column so the uniqueness suffix always fits.
    stem = stem[:48]
    return stem or "workspace"


class TenantProvisioningService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, actor: ActorContext, name: str) -> Tenant:
        name = name.strip()
        if not 2 <= len(name) <= 200:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="tenant_name_invalid"
            )
        owned = await self.session.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(
                TenantMembership.user_id == actor.actor_id,
                TenantMembership.role == Role.OWNER.value,
                TenantMembership.status == "active",
            )
        )
        if (owned or 0) >= MAX_OWNED_TENANTS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="tenant_limit_reached"
            )

        # `tenant.created_by` and `tenant_membership.user_id` are both foreign
        # keys to `app_user`. Through the real sign-in path the actor always is
        # one; the development pilot context carries a fixed id that is not, and
        # naming it would fail the insert on the foreign key rather than on
        # anything meaningful.
        creator = await self.session.scalar(
            select(AppUser.id).where(AppUser.id == actor.actor_id)
        )
        if creator is None:
            # A membership must name a real user -- it is what grants access --
            # so unlike `created_by` this cannot be softened to null.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="actor_not_a_user"
            )
        tenant = await self._insert_tenant(name, creator)

        # The membership and the audit row below live under row-level security
        # keyed on `app.tenant_id`. The tenant did not exist when this
        # transaction opened, so nothing has set that yet; set it now, to the
        # tenant we just made. It widens the scope to exactly one tenant -- the
        # empty one this person now owns -- and it is transaction-local, so it
        # does not leak into the next request on a pooled connection.
        await self.session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant.id)},
        )

        self.session.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=actor.actor_id,
                role=Role.OWNER.value,
                status="active",
                # Nobody invited them. Recording ourselves as the inviter would
                # make the audit trail claim a grant that never happened.
                invited_by=None,
            )
        )
        payload: dict[str, object] = {
            "tenant_id": str(tenant.id),
            "slug": tenant.slug,
            "name": tenant.name,
            "created_by": str(actor.actor_id),
        }
        self.session.add(
            AuditEvent(
                tenant_id=tenant.id,
                actor_type="user",
                actor_id=str(actor.actor_id),
                action="tenant.created",
                resource_type="tenant",
                resource_id=str(tenant.id),
                trace_id=actor.trace_id,
                metadata_json=payload,
                event_hash=stable_hash(payload),
            )
        )
        await self.session.flush()
        return tenant

    async def _insert_tenant(self, name: str, created_by: UUID) -> Tenant:
        """Insert under a derived slug, retrying past a collision.

        Two people naming their workspace the same thing is ordinary, not an
        error, so the second one gets a suffix rather than a refusal.
        """
        stem = slugify(name)
        for attempt in range(6):
            slug = stem if attempt == 0 else f"{stem}-{secrets.token_hex(3)}"
            tenant = Tenant(
                slug=slug,
                name=name,
                status="active",
                created_by=created_by,
                created_at=datetime.now(UTC),
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(tenant)
                    await self.session.flush()
                return tenant
            except IntegrityError:
                continue
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="tenant_slug_unavailable"
        )
