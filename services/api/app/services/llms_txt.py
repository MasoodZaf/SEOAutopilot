"""Propose an llms.txt for a site that does not serve one.

The file is built from the latest crawl's indexable pages and becomes an
ordinary new-file proposal: high risk, two approvers who are not its author,
and deployed as a pull request a person merges. Nothing here writes to the
repository.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import TenantContext
from app.db.models import Connector, CrawlJob, Page, PageObservation, Proposal, Site
from app.domain.llms_txt import MAX_LINKS, LlmsPage, render_llms_txt
from app.services.proposals import ProposalService

# Proposal states that mean an llms.txt is already on its way or live.
OPEN_PROPOSAL_STATES = (
    "draft", "validating", "validated", "review_required", "approved", "deploying", "deployed",
)


def _llms_status(summary: Any) -> str | None:
    if isinstance(summary, str):
        summary = json.loads(summary)
    access = summary.get("ai_access") if isinstance(summary, dict) else None
    llms = access.get("llms_txt") if isinstance(access, dict) else None
    value = llms.get("status") if isinstance(llms, dict) else None
    return value if isinstance(value, str) else None


class LlmsTxtService:
    def __init__(self, session: AsyncSession, context: TenantContext, settings: Settings) -> None:
        self.session = session
        self.context = context
        self.settings = settings

    async def propose(self, site_id: UUID) -> Proposal:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == site.id,
                Connector.type == "github_repository",
                Connector.status == "active",
            )
        )
        if connector is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="github_not_connected")
        target_path = str(
            (connector.config_json or {}).get("llms_txt_path") or self.settings.github_llms_txt_path
        ).strip()
        if not target_path.endswith("llms.txt"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="llms_txt_path_invalid"
            )

        crawl = await self.session.scalar(
            select(CrawlJob)
            .where(
                CrawlJob.tenant_id == self.context.tenant_id,
                CrawlJob.site_id == site.id,
                CrawlJob.status.in_(("completed", "partial")),
                CrawlJob.finished_at.is_not(None),
            )
            .order_by(CrawlJob.finished_at.desc(), CrawlJob.id.desc())
            .limit(1)
        )
        if crawl is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="crawl_required")
        llms_status = _llms_status(crawl.result_summary)
        if llms_status is None:
            # A crawl from before llms.txt was checked: we do not know whether
            # the site already serves one, so we do not propose overwriting it.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="recrawl_required")
        if llms_status != "missing":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"llms_txt_{llms_status}"
            )

        existing = await self.session.scalar(
            select(Proposal.id).where(
                Proposal.tenant_id == self.context.tenant_id,
                Proposal.site_id == site.id,
                Proposal.generator == "llms_txt",
                Proposal.status.in_(OPEN_PROPOSAL_STATES),
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="llms_txt_proposal_exists"
            )

        rows = (
            await self.session.execute(
                select(Page.normalized_url, PageObservation.title, PageObservation.meta_description)
                .join(
                    Page,
                    (Page.id == PageObservation.page_id)
                    & (Page.tenant_id == PageObservation.tenant_id),
                )
                .where(
                    PageObservation.tenant_id == self.context.tenant_id,
                    PageObservation.crawl_job_id == crawl.id,
                    PageObservation.http_status == 200,
                    ~PageObservation.robots_directives.contains(["noindex"]),
                    (PageObservation.canonical_url.is_(None))
                    | (PageObservation.canonical_url == Page.normalized_url),
                )
                .order_by(Page.normalized_url)
                .limit(MAX_LINKS * 2)
            )
        ).all()
        pages = [LlmsPage(url=row[0], title=row[1], description=row[2]) for row in rows]
        if not any(page.title for page in pages):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="no_indexable_pages"
            )
        body = render_llms_txt(site.name, site.canonical_origin, pages)

        # The URL the file will be served at, named the way the crawler names
        # pages, so verification checks the live file.
        url = site.canonical_origin.rstrip("/") + "/llms.txt"
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        page = await self.session.scalar(
            select(Page).where(Page.site_id == site.id, Page.url_hash == url_hash)
        )
        if page is None:
            page = Page(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                normalized_url=url,
                url_hash=url_hash,
                lifecycle_status="planned",
            )
            self.session.add(page)
            await self.session.flush()

        listed = body.count("\n- [")
        try:
            async with self.session.begin_nested():
                return await self._create(site, page.id, crawl.id, target_path, body, listed)
        except IntegrityError as error:
            # Another request won the race; the unique index kept it to one.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="llms_txt_proposal_exists"
            ) from error

    async def _create(
        self, site: Site, page_id: UUID, crawl_id: UUID, target_path: str, body: str, listed: int
    ) -> Proposal:
        return await ProposalService(self.session, self.context).create_new_page(
            site,
            page_id=page_id,
            content_draft_id=None,
            title="Add llms.txt",
            rationale=(
                f"The last crawl found no /llms.txt. This lists {listed} indexable page(s) "
                "with the titles and descriptions the site already publishes, so AI "
                "assistants can find its main pages. Some AI tools read llms.txt; no "
                "answer engine has said it uses the file for ranking or citation."
            ),
            target_path=target_path,
            after_content=body,
            evidence_refs={
                "crawl_job_id": str(crawl_id),
                "pages_listed": listed,
                "generator_version": "llms_txt.v1",
            },
            generator="llms_txt",
        )
