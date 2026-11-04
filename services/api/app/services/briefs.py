"""Content brief read model and status transitions.

A brief is advisory: moving it through the queue records intent and nothing
else. It never deploys, and turning one into a change still goes through the
proposal lifecycle and its approval gates.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ContentBriefStatus, ContentBriefStatusUpdate, TopicBriefCreate
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, ContentBrief, Site
from app.domain.post_topics import SitePage, content_hash, covering_page, topic_sections
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

    async def create_topic(self, site_id: UUID, command: TopicBriefCreate) -> ContentBrief:
        """A new-post brief for a topic a person names.

        Refused when a page the site already has targets the topic: a post
        there would compete with that page, so the page is named instead.
        """
        if self.context.role not in MANAGE_BRIEF_ROLES:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_brief",
            )
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="site_not_found")

        pages = await self._site_pages(site_id)
        covered = covering_page(command.topic, pages)
        if covered is not None:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"topic_already_has_page:{covered.url}",
            )

        intent = command.intent.value
        sections = topic_sections(command.topic, intent)
        brief = ContentBrief(
            tenant_id=self.context.tenant_id,
            site_id=site_id,
            source="topic",
            requested_by=self.context.actor_id,
            kind="new_page",
            status="queued",
            cluster_label=command.topic,
            intent=intent,
            answer_engine_candidate=False,
            priority_score=0.0,
            sections_json=sections,
            evidence_json={"source": "topic", "selection_basis": "requested"},
            query_hashes=[],
            content_hash=content_hash(sections),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(brief)
                await self.session.flush()
        except IntegrityError as error:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT, detail="topic_brief_exists"
            ) from error

        payload = {"topic": command.topic, "intent": intent, "site_id": str(site_id)}
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="content_brief.requested",
                resource_type="content_brief",
                resource_id=str(brief.id),
                trace_id=self.context.trace_id,
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        await self.session.refresh(brief)
        return brief

    async def _site_pages(self, site_id: UUID) -> list[SitePage]:
        """Each page's title from the site's latest finished crawl."""
        rows = await self.session.execute(
            text(
                """
                SELECT DISTINCT ON (p.normalized_url) p.normalized_url, coalesce(o.title,'') AS title
                FROM page_observation o
                JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
                WHERE o.tenant_id=:tenant AND p.site_id=:site AND o.http_status=200
                  AND o.crawl_job_id=(
                    SELECT id FROM crawl_job WHERE tenant_id=:tenant AND site_id=:site
                      AND status IN('completed','partial') ORDER BY created_at DESC LIMIT 1)
                ORDER BY p.normalized_url
                """
            ),
            {"tenant": self.context.tenant_id, "site": site_id},
        )
        return [SitePage(url=str(row.normalized_url), title=str(row.title)) for row in rows]

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
        await self.session.flush()
        await self.session.refresh(brief)
        return brief
