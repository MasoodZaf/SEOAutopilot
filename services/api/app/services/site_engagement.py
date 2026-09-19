"""What visitors did once they arrived, from the site's GA4 property.

The Analytics connector has synced into `analytics_metric` since 2026-09-07,
and the only reader was the weekly digest, which the dashboard never rendered.
A permission a person granted should produce something they can see; this is
that summary.

Deliberately not set beside Search Console clicks: GA4 sessions and GSC clicks
count different things, and a side-by-side invites subtracting one from the
other (see `routines/reports.py` in the worker).
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.db.models import Site

_TOTALS_SQL = text(
    """
    SELECT COUNT(*) AS rows,
           COALESCE(SUM(sessions), 0) AS sessions,
           COALESCE(SUM(engaged_sessions), 0) AS engaged_sessions,
           COALESCE(SUM(views), 0) AS views,
           COALESCE(SUM(key_events), 0) AS key_events
    FROM analytics_metric
    WHERE tenant_id = :tenant_id AND site_id = :site_id
      AND metric_date BETWEEN :range_start AND :range_end
    """
)

_LANDING_PAGES_SQL = text(
    """
    SELECT landing_page,
           SUM(sessions) AS sessions,
           SUM(engaged_sessions) AS engaged_sessions
    FROM analytics_metric
    WHERE tenant_id = :tenant_id AND site_id = :site_id
      AND metric_date BETWEEN :range_start AND :range_end
    GROUP BY landing_page
    ORDER BY SUM(sessions) DESC, landing_page
    LIMIT 5
    """
)


@dataclass(frozen=True)
class LandingPage:
    landing_page: str
    sessions: float
    engaged_sessions: float


@dataclass(frozen=True)
class EngagementSummary:
    range_start: date
    range_end: date
    rows: int
    sessions: float
    engaged_sessions: float
    engagement_rate: float | None
    views: float
    key_events: float
    top_landing_pages: list[LandingPage]


class SiteEngagementService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def summarize(
        self, site_id: UUID, range_start: date, range_end: date
    ) -> EngagementSummary:
        if range_end < range_start or (range_end - range_start).days > 89:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid_range"
            )
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        params = {
            "tenant_id": self.context.tenant_id,
            "site_id": site.id,
            "range_start": range_start,
            "range_end": range_end,
        }
        totals = (await self.session.execute(_TOTALS_SQL, params)).one()
        pages = (await self.session.execute(_LANDING_PAGES_SQL, params)).all()
        sessions = float(totals.sessions)
        engaged = float(totals.engaged_sessions)
        return EngagementSummary(
            range_start=range_start,
            range_end=range_end,
            rows=int(totals.rows),
            sessions=sessions,
            engaged_sessions=engaged,
            # A rate over no sessions is undefined, not zero.
            engagement_rate=engaged / sessions if sessions else None,
            views=float(totals.views),
            key_events=float(totals.key_events),
            top_landing_pages=[
                LandingPage(
                    landing_page=row.landing_page,
                    sessions=float(row.sessions),
                    engaged_sessions=float(row.engaged_sessions),
                )
                for row in pages
            ],
        )
