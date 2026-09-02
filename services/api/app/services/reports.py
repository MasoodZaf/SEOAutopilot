from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.db.models import Report, Site

MAX_REPORT_PAGE_SIZE = 100


class ReportService:
    """Read model over reports produced by the worker's routine runner."""

    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def _site_exists(self, site_id: UUID) -> bool:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        return site is not None

    async def list_for_site(
        self, site_id: UUID, limit: int, kind: str | None = None
    ) -> list[Report] | None:
        if not await self._site_exists(site_id):
            return None
        filters = [Report.tenant_id == self.context.tenant_id, Report.site_id == site_id]
        if kind:
            filters.append(Report.kind == kind)
        result = await self.session.scalars(
            select(Report)
            .where(*filters)
            .order_by(Report.generated_at.desc(), Report.id.desc())
            .limit(min(limit, MAX_REPORT_PAGE_SIZE))
        )
        return list(result)

    async def get(self, report_id: UUID) -> Report | None:
        return await self.session.scalar(
            select(Report).where(
                Report.id == report_id, Report.tenant_id == self.context.tenant_id
            )
        )

    async def latest(self, site_id: UUID, kind: str) -> Report | None:
        return await self.session.scalar(
            select(Report)
            .where(
                Report.tenant_id == self.context.tenant_id,
                Report.site_id == site_id,
                Report.kind == kind,
            )
            .order_by(Report.generated_at.desc(), Report.id.desc())
            .limit(1)
        )
