from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.core.cursors import PageCursor
from app.db.models import Finding, Page, PageObservation, PageScore, ScoringVersion, Site


@dataclass(frozen=True, slots=True)
class PageEvidenceRecord:
    page: Page
    observation: PageObservation | None
    score: PageScore | None
    scoring_version: str | None
    findings: list[Finding]


class PageService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def list_pages(
        self,
        site_id: UUID,
        limit: int,
        cursor: PageCursor | None,
    ) -> tuple[list[Page], PageCursor | None] | None:
        site = await self.session.scalar(
            select(Site.id).where(
                Site.id == site_id,
                Site.tenant_id == self.context.tenant_id,
            )
        )
        if site is None:
            return None
        query = select(Page).where(
            Page.tenant_id == self.context.tenant_id,
            Page.site_id == site_id,
        )
        if cursor is not None:
            query = query.where(
                or_(
                    Page.last_seen_at < cursor.last_seen_at,
                    and_(
                        Page.last_seen_at == cursor.last_seen_at,
                        Page.id < cursor.page_id,
                    ),
                )
            )
        result = await self.session.scalars(
            query.order_by(Page.last_seen_at.desc(), Page.id.desc()).limit(limit + 1)
        )
        fetched = list(result)
        pages = fetched[:limit]
        next_cursor = None
        if len(fetched) > limit and pages:
            last = pages[-1]
            next_cursor = PageCursor(
                site_id=site_id,
                last_seen_at=last.last_seen_at,
                page_id=last.id,
            )
        return pages, next_cursor

    async def get_page(self, page_id: UUID) -> PageEvidenceRecord | None:
        page = await self.session.scalar(
            select(Page).where(
                Page.id == page_id,
                Page.tenant_id == self.context.tenant_id,
            )
        )
        if page is None:
            return None
        observation = await self.session.scalar(
            select(PageObservation)
            .where(
                PageObservation.page_id == page.id,
                PageObservation.tenant_id == self.context.tenant_id,
            )
            .order_by(PageObservation.observed_at.desc(), PageObservation.id.desc())
            .limit(1)
        )
        score_result = await self.session.execute(
            select(PageScore, ScoringVersion.code_version)
            .join(ScoringVersion, ScoringVersion.id == PageScore.scoring_version_id)
            .where(
                PageScore.page_id == page.id,
                PageScore.tenant_id == self.context.tenant_id,
                ScoringVersion.retired_at.is_(None),
            )
            .order_by(PageScore.evidence_cutoff.desc(), PageScore.id.desc())
            .limit(1)
        )
        score_row = score_result.first()
        score = score_row[0] if score_row else None
        scoring_version = score_row[1] if score_row else None
        findings: list[Finding] = []
        if score is not None:
            result = await self.session.scalars(
                select(Finding)
                .where(
                    Finding.page_id == page.id,
                    Finding.tenant_id == self.context.tenant_id,
                    Finding.scoring_version_id == score.scoring_version_id,
                    Finding.status == "open",
                )
                .order_by(Finding.severity, Finding.rule_key, Finding.id)
            )
            findings = list(result)
        return PageEvidenceRecord(page, observation, score, scoring_version, findings)
