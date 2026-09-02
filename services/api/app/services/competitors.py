"""Competitor register and scan read model.

A competitor and each tracked page are entered by a human. Nothing here
discovers URLs, and the scan that consumes these rows requests only what is
stored, so adding a competitor never widens what the platform will fetch beyond
what somebody explicitly asked for.
"""

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CompetitorCreate, CompetitorPageCreate
from app.core.context import Role, TenantContext
from app.db.models import (
    AiVisibilitySnapshot,
    AuditEvent,
    Competitor,
    CompetitorObservation,
    CompetitorPage,
    CompetitorScan,
    Site,
)
from app.services.opportunities import stable_hash

MANAGE_COMPETITOR_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER}
MAX_COMPETITORS_PER_SITE = 20
MAX_PAGES_PER_COMPETITOR = 25


class CompetitorService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def _require_site(self, site_id: UUID) -> Site:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        return site

    def _require_role(self) -> None:
        if self.context.role not in MANAGE_COMPETITOR_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_competitor",
            )

    async def list_competitors(self, site_id: UUID) -> list[Competitor] | None:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            return None
        result = await self.session.scalars(
            select(Competitor)
            .where(Competitor.tenant_id == self.context.tenant_id, Competitor.site_id == site_id)
            .order_by(Competitor.normalized_host)
        )
        return list(result)

    async def create_competitor(self, site_id: UUID, command: CompetitorCreate) -> Competitor:
        self._require_role()
        site = await self._require_site(site_id)
        host = (urlsplit(command.origin).hostname or "").lower()
        if host == site.normalized_host:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="competitor_is_the_site_itself"
            )
        existing = await self.list_competitors(site_id) or []
        if len(existing) >= MAX_COMPETITORS_PER_SITE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="competitor_limit_reached"
            )

        competitor = Competitor(
            tenant_id=self.context.tenant_id,
            site_id=site_id,
            normalized_host=host,
            label=command.label,
            created_by=self.context.actor_id,
        )
        # Savepoint, not a session rollback: a session rollback would end the
        # request transaction and drop the tenant GUC scoping every later write.
        try:
            async with self.session.begin_nested():
                self.session.add(competitor)
                await self.session.flush()
        except IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="competitor_already_tracked"
            ) from error

        payload = {"normalized_host": host, "label": command.label}
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="competitor.added",
                resource_type="competitor",
                resource_id=str(competitor.id),
                trace_id=self.context.trace_id,
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        await self.session.refresh(competitor)
        return competitor

    async def list_pages(self, competitor_id: UUID) -> list[CompetitorPage] | None:
        competitor = await self.session.scalar(
            select(Competitor.id).where(
                Competitor.id == competitor_id, Competitor.tenant_id == self.context.tenant_id
            )
        )
        if competitor is None:
            return None
        result = await self.session.scalars(
            select(CompetitorPage)
            .where(
                CompetitorPage.tenant_id == self.context.tenant_id,
                CompetitorPage.competitor_id == competitor_id,
            )
            .order_by(CompetitorPage.normalized_url)
        )
        return list(result)

    async def add_page(
        self, competitor_id: UUID, command: CompetitorPageCreate
    ) -> CompetitorPage:
        self._require_role()
        competitor = await self.session.scalar(
            select(Competitor).where(
                Competitor.id == competitor_id, Competitor.tenant_id == self.context.tenant_id
            )
        )
        if competitor is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="competitor_not_found"
            )
        host = (urlsplit(command.url).hostname or "").lower()
        if host != competitor.normalized_host:
            # A page filed under a competitor must belong to that competitor.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="url_host_does_not_match_competitor"
            )
        existing = await self.list_pages(competitor_id) or []
        if len(existing) >= MAX_PAGES_PER_COMPETITOR:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="competitor_page_limit_reached"
            )

        page = CompetitorPage(
            tenant_id=self.context.tenant_id,
            competitor_id=competitor_id,
            site_id=competitor.site_id,
            normalized_url=command.url,
            url_hash=hashlib.sha256(command.url.encode()).hexdigest(),
            keyword_cluster_key=command.keyword_cluster_key,
            created_by=self.context.actor_id,
        )
        # Savepoint, not a session rollback: a session rollback would end the
        # request transaction and drop the tenant GUC scoping every later write.
        try:
            async with self.session.begin_nested():
                self.session.add(page)
                await self.session.flush()
        except IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="competitor_page_already_tracked"
            ) from error
        await self.session.flush()
        await self.session.refresh(page)
        return page

    async def remove_competitor(self, competitor_id: UUID) -> None:
        self._require_role()
        competitor = await self.session.scalar(
            select(Competitor).where(
                Competitor.id == competitor_id, Competitor.tenant_id == self.context.tenant_id
            )
        )
        if competitor is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="competitor_not_found"
            )
        competitor.status = "paused"
        competitor.updated_at = datetime.now(UTC)
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="competitor.paused",
                resource_type="competitor",
                resource_id=str(competitor.id),
                trace_id=self.context.trace_id,
                metadata_json={"normalized_host": competitor.normalized_host},
                event_hash=stable_hash(
                    {
                        "normalized_host": competitor.normalized_host,
                        "actor_id": str(self.context.actor_id),
                    }
                ),
            )
        )
        await self.session.flush()

    async def latest_scan(
        self, site_id: UUID
    ) -> tuple[CompetitorScan, list[CompetitorObservation]] | None:
        scan = await self.session.scalar(
            select(CompetitorScan)
            .where(
                CompetitorScan.tenant_id == self.context.tenant_id,
                CompetitorScan.site_id == site_id,
            )
            .order_by(CompetitorScan.started_at.desc(), CompetitorScan.id.desc())
            .limit(1)
        )
        if scan is None:
            return None
        observations = await self.session.scalars(
            select(CompetitorObservation)
            .where(
                CompetitorObservation.tenant_id == self.context.tenant_id,
                CompetitorObservation.competitor_scan_id == scan.id,
            )
            .order_by(CompetitorObservation.competitor_page_id)
        )
        return scan, list(observations)

    async def ai_visibility_history(
        self, site_id: UUID, limit: int
    ) -> list[AiVisibilitySnapshot] | None:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            return None
        result = await self.session.scalars(
            select(AiVisibilitySnapshot)
            .where(
                AiVisibilitySnapshot.tenant_id == self.context.tenant_id,
                AiVisibilitySnapshot.site_id == site_id,
            )
            .order_by(AiVisibilitySnapshot.captured_on.desc())
            .limit(min(limit, 90))
        )
        return list(result)
