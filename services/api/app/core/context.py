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
