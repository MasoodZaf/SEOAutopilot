from dataclasses import dataclass
from datetime import date
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.db.models import SearchMetric, Site


@dataclass(frozen=True)
class SearchPerformanceSummary:
    range_start: date
    range_end: date
    rows: int
    clicks: float
    impressions: float
    ctr: float | None
    position: float | None
    is_sparse: bool


class SearchPerformanceService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def summarize(
        self, site_id: UUID, range_start: date, range_end: date
    ) -> SearchPerformanceSummary:
        if range_end < range_start or (range_end - range_start).days > 89:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_range")
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        result = await self.session.execute(
            select(
                func.count(SearchMetric.id).label("rows"),
                func.coalesce(func.sum(SearchMetric.clicks), 0.0).label("clicks"),
                func.coalesce(func.sum(SearchMetric.impressions), 0.0).label("impressions"),
                func.sum(SearchMetric.position * SearchMetric.impressions).label(
                    "weighted_position"
                ),
            ).where(
                SearchMetric.tenant_id == self.context.tenant_id,
                SearchMetric.site_id == site.id,
                SearchMetric.metric_date >= range_start,
                SearchMetric.metric_date <= range_end,
            )
        )
        row = result.one()
        rows = int(row.rows or 0)
        clicks = float(row.clicks or 0.0)
        impressions = float(row.impressions or 0.0)
        return SearchPerformanceSummary(
            range_start=range_start,
            range_end=range_end,
            rows=rows,
            clicks=clicks,
            impressions=impressions,
            ctr=clicks / impressions if impressions else None,
            position=float(row.weighted_position) / impressions
            if impressions and row.weighted_position is not None
            else None,
            is_sparse=impressions < 100,
        )
