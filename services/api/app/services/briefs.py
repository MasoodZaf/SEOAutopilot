"""Content brief read model and status transitions.

A brief is advisory: moving it through the queue records intent and nothing
else. It never deploys, and turning one into a change still goes through the
proposal lifecycle and its approval gates.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ContentBriefStatus, ContentBriefStatusUpdate
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, ContentBrief, Site
from app.services.opportunities import stable_hash

MANAGE_BRIEF_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR}
MAX_BRIEF_PAGE_SIZE = 100

# A dismissed brief is terminal for this analysis run; the next run rebuilds it
# from fresh evidence rather than resurrecting the old judgement here.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"in_progress", "done", "dismissed"},
    "in_progress": {"queued", "done", "dismissed"},
    "done": {"in_progress"},
    "dismissed": {"queued"},
}


class ContentBriefService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def list_for_site(
        self,
        site_id: UUID,
        limit: int,
        brief_status: str | None = None,
        kind: str | None = None,
        answer_engine_only: bool = False,
    ) -> list[ContentBrief] | None:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            return None
        filters = [
            ContentBrief.tenant_id == self.context.tenant_id,
            ContentBrief.site_id == site_id,
        ]
        if brief_status:
            filters.append(ContentBrief.status == brief_status)
        if kind:
            filters.append(ContentBrief.kind == kind)
        if answer_engine_only:
            filters.append(ContentBrief.answer_engine_candidate.is_(True))
        result = await self.session.scalars(
            select(ContentBrief)
            .where(*filters)
            .order_by(ContentBrief.priority_score.desc(), ContentBrief.id)
            .limit(min(limit, MAX_BRIEF_PAGE_SIZE))
        )
        return list(result)

    async def get(self, brief_id: UUID) -> ContentBrief | None:
        return await self.session.scalar(
            select(ContentBrief).where(
                ContentBrief.id == brief_id,
                ContentBrief.tenant_id == self.context.tenant_id,
            )
        )

    async def update_status(
        self, brief_id: UUID, command: ContentBriefStatusUpdate
    ) -> ContentBrief:
        if self.context.role not in MANAGE_BRIEF_ROLES:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_brief",
            )
        brief = await self.get(brief_id)
        if brief is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND, detail="content_brief_not_found"
            )
        target = command.status.value
        if target == brief.status:
            return brief
        if target not in ALLOWED_TRANSITIONS.get(brief.status, set()):
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="content_brief_transition_not_allowed",
            )

        now = datetime.now(UTC)
        previous = brief.status
        brief.status = target
        brief.updated_at = now
        if command.status is ContentBriefStatus.DISMISSED:
            brief.dismissed_reason = command.reason.strip()
            brief.dismissed_by = self.context.actor_id
            brief.dismissed_at = now
        else:
            brief.dismissed_reason = None
            brief.dismissed_by = None
            brief.dismissed_at = None

        payload = {
            "previous_status": previous,
            "status": target,
            "cluster_label": brief.cluster_label,
            "reason": command.reason.strip(),
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="content_brief.status_changed",
                resource_type="content_brief",
                resource_id=str(brief.id),
                trace_id=self.context.trace_id,
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.commit()
        await self.session.refresh(brief)
        return brief
