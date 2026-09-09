from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from fastapi import HTTPException, status


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    SEO_MANAGER = "seo_manager"
    EDITOR = "editor"
    DEVELOPER = "developer"
    VIEWER = "viewer"


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID
    actor_id: UUID
    role: Role
    trace_id: str

    def require(self, *roles: Role) -> None:
        if self.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


@dataclass(frozen=True, slots=True)
class ActorContext:
    """A person, proven, with no authority over anybody's data yet.

    The narrower half of `TenantContext`, and the only thing that can be
    established about somebody who has just signed in for the first time. It
    exists for the two requests that must work before a membership does:
    asking which tenants you are in, and creating your first one.

    It deliberately carries no tenant id. A route that takes this cannot read
    tenant data by accident, because it has no scope to read it under.
    """

    actor_id: UUID
    email: str
    display_name: str
    trace_id: str
