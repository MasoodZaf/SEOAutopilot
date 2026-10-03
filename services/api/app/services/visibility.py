"""The zero-click scorecard: what grows when nobody clicks.

Most searches now end on the results page, and AI answers settle questions
before anyone reaches a site. Clicks alone undercount whether a site is
gaining attention, so this reads four things side by side, week by week:

* branded demand -- Search Console impressions and clicks for queries that
  name the site. People asking for a site by name is the plainest sign that
  attention turned into demand.
* zero-click queries -- queries shown often enough to matter that produced no
  click: the results page (or an AI overview) already answered them.
* direct visits -- GA4 sessions in the Direct channel, a rough proxy for
  people who came back without searching.
* AI answers -- per citation run, how often the engines named or cited the
  site, and how often they cited a tracked competitor.

None of it is causal and the page must not present it so. It describes a
trend; it does not attribute one to any change.

Search terms stay sealed. Telling branded from non-branded needs the term, so
the terms are opened here, in the API process, tested against the site's
brand terms, and dropped; only weekly totals leave this module. That is why
no role gate or `keyword_terms.read` audit applies: no term is revealed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Date, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Role, TenantContext
from app.db.models import (
    AiCitationObservation,
    AiCitationRun,
    AuditEvent,
    SearchMetric,
    SearchQuery,
    Site,
)
from app.services.keywords import decrypt_term
from app.services.opportunities import stable_hash

# A query shown fewer times than this in a week is too rare to call "answered
# on the results page"; one impression with no click is noise.
ZERO_CLICK_MIN_IMPRESSIONS = 5
# Below this many impressions across the whole window the trend is mostly
# noise, and the page says so instead of drawing it.
SPARSE_IMPRESSIONS = 200
MAX_WEEKS = 26
MAX_BRAND_TERMS = 10
# Search Console data settles about two days behind.
SEARCH_LAG_DAYS = 2
BRAND_TERM_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER}

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def compact(text: str) -> str:
    """Lowercase letters and digits only, so "Calc Hive" and "calchive" meet."""
    return _NON_ALNUM.sub("", text.casefold())


def default_brand_terms(site_name: str, site_host: str) -> list[str]:
    """The names the citation scan already recognises the site by.

    The site's name and its bare domain label (the worker's `brand_terms`),
    plus the name without a leading "the", which searchers usually drop.
    """
    host = site_host.strip().lower().removeprefix("www.")
    candidates = [site_name, host.split(".")[0]]
    name = compact(site_name)
    if name.startswith("the") and len(name) > 6:
        candidates.append(name[3:])
    terms: list[str] = []
    for candidate in candidates:
        term = compact(candidate)
        if len(term) >= 4 and term not in terms:
            terms.append(term)
    return terms


def brand_terms_for(site: Site) -> list[str]:
    stored = [compact(term) for term in site.brand_terms or [] if isinstance(term, str)]
    stored = [term for term in stored if len(term) >= 3]
    return stored or default_brand_terms(site.name, site.normalized_host)


def is_branded(term: str, brand: list[str]) -> bool:
    flat = compact(term)
    return any(name in flat for name in brand)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass
class WeekRow:
    week_start: date
    clicks: float = 0.0
    impressions: float = 0.0
    branded_clicks: float | None = None
    branded_impressions: float | None = None
    zero_click_queries: int = 0
    zero_click_impressions: float = 0.0
    direct_sessions: float | None = None
    sessions: float | None = None


@dataclass(frozen=True)
class CitationPoint:
    run_id: UUID
    finished_at: datetime
    answers: int
    named: int
    cited: int
    competitor_cited: int


@dataclass
class VisibilityTrend:
    weeks: list[WeekRow]
    citation_runs: list[CitationPoint]
    brand_terms: list[str]
    brand_terms_derived: bool
    branded_available: bool
    search_connected: bool
    analytics_connected: bool
    sparse: bool
    notes: list[str] = field(default_factory=list)


class VisibilityService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        encryption_key: bytes | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.encryption_key = encryption_key

    async def _site(self, site_id: UUID) -> Site:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        return site

    async def _branded_hashes(self, site: Site, brand: list[str]) -> set[str] | None:
        """Query hashes whose term names the brand; None when terms cannot be opened."""
        if self.encryption_key is None:
            return None
        stored = await self.session.scalars(
            select(SearchQuery).where(
                SearchQuery.tenant_id == self.context.tenant_id,
                SearchQuery.site_id == site.id,
            )
        )
        branded: set[str] = set()
        opened = 0
        for row in stored:
            term = decrypt_term(self.encryption_key, row)
            if term is None:
                continue
            opened += 1
            if is_branded(term, brand):
                branded.add(row.query_hash)
        # Rows exist but none opened: a rotated key. Say "unknown", not "zero".
        total = await self.session.scalar(
            select(func.count()).select_from(SearchQuery).where(
                SearchQuery.tenant_id == self.context.tenant_id,
                SearchQuery.site_id == site.id,
            )
        )
        if total and not opened:
            return None
        return branded

    async def trend(self, site_id: UUID, weeks: int = 12, today: date | None = None) -> VisibilityTrend:
        if not 1 <= weeks <= MAX_WEEKS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_weeks")
        site = await self._site(site_id)
        brand = brand_terms_for(site)
        today = today or datetime.now(UTC).date()
        last = today - timedelta(days=SEARCH_LAG_DAYS)
        first_week = week_start(last) - timedelta(weeks=weeks - 1)
        rows = {first_week + timedelta(weeks=index): WeekRow(first_week + timedelta(weeks=index)) for index in range(weeks)}

        week = cast(func.date_trunc("week", SearchMetric.metric_date), Date)
        per_query = await self.session.execute(
            select(
                week.label("week"),
                SearchMetric.query_hash,
                func.sum(SearchMetric.clicks).label("clicks"),
                func.sum(SearchMetric.impressions).label("impressions"),
            )
            .where(
                SearchMetric.tenant_id == self.context.tenant_id,
                SearchMetric.site_id == site.id,
                SearchMetric.metric_date >= first_week,
                SearchMetric.metric_date <= last,
            )
            .group_by(week, SearchMetric.query_hash)
        )
        per_query_rows = per_query.all()
        branded = await self._branded_hashes(site, brand) if per_query_rows else set()
        for bucket, query_hash, clicks, impressions in per_query_rows:
            row = rows.get(bucket)
            if row is None:
                continue
            row.clicks += clicks or 0
            row.impressions += impressions or 0
            if branded is not None:
                row.branded_clicks = row.branded_clicks or 0.0
                row.branded_impressions = row.branded_impressions or 0.0
                if query_hash in branded:
                    row.branded_clicks += clicks or 0
                    row.branded_impressions += impressions or 0
            if (impressions or 0) >= ZERO_CLICK_MIN_IMPRESSIONS and not clicks:
                row.zero_click_queries += 1
                row.zero_click_impressions += impressions or 0
        if branded is not None:
            for row in rows.values():
                row.branded_clicks = row.branded_clicks or 0.0
                row.branded_impressions = row.branded_impressions or 0.0

        analytics_connected = await self._direct_sessions(site.id, rows, first_week, last)
        citation_runs = await self._citation_runs(site.id, first_week)

        total_impressions = sum(row.impressions for row in rows.values())
        notes: list[str] = []
        if per_query_rows and branded is None:
            notes.append("branded_split_unavailable")
        return VisibilityTrend(
            weeks=[rows[key] for key in sorted(rows)],
            citation_runs=citation_runs,
            brand_terms=brand,
            brand_terms_derived=not site.brand_terms,
            branded_available=branded is not None,
            search_connected=bool(per_query_rows),
            analytics_connected=analytics_connected,
            sparse=total_impressions < SPARSE_IMPRESSIONS,
            notes=notes,
        )

    async def _direct_sessions(
        self, site_id: UUID, rows: dict[date, WeekRow], first_week: date, last: date
    ) -> bool:
        # analytics_metric is not mapped as a model; it is written by the worker
        # and read here the way site_engagement reads it.
        result = await self.session.execute(
            text(
                """
                SELECT date_trunc('week', metric_date)::date AS week,
                       COALESCE(SUM(sessions), 0) AS sessions,
                       COALESCE(SUM(sessions) FILTER (WHERE channel_group = 'Direct'), 0) AS direct
                FROM analytics_metric
                WHERE tenant_id = :tenant_id AND site_id = :site_id
                  AND metric_date BETWEEN :first AND :last
                GROUP BY 1
                """
            ),
            {"tenant_id": self.context.tenant_id, "site_id": site_id, "first": first_week, "last": last},
        )
        found = result.all()
        if not found:
            return False
        for row in rows.values():
            row.sessions = 0.0
            row.direct_sessions = 0.0
        for bucket, sessions, direct in found:
            row = rows.get(bucket)
            if row is not None:
                row.sessions = float(sessions)
                row.direct_sessions = float(direct)
        return True

    async def _citation_runs(self, site_id: UUID, first_week: date) -> list[CitationPoint]:
        answered = AiCitationObservation.status == "answered"
        result = await self.session.execute(
            select(
                AiCitationRun.id,
                AiCitationRun.finished_at,
                func.count(AiCitationObservation.id).filter(answered).label("answers"),
                func.count(AiCitationObservation.id)
                .filter(answered, (AiCitationObservation.site_cited | AiCitationObservation.site_mentioned))
                .label("named"),
                func.count(AiCitationObservation.id).filter(answered, AiCitationObservation.site_cited).label("cited"),
                func.count(AiCitationObservation.id)
                .filter(answered, func.jsonb_array_length(AiCitationObservation.competitor_hosts) > 0)
                .label("competitor_cited"),
            )
            .join(
                AiCitationObservation,
                (AiCitationObservation.run_id == AiCitationRun.id)
                & (AiCitationObservation.tenant_id == AiCitationRun.tenant_id),
            )
            .where(
                AiCitationRun.tenant_id == self.context.tenant_id,
                AiCitationRun.site_id == site_id,
                AiCitationRun.finished_at.is_not(None),
                AiCitationRun.finished_at >= datetime.combine(first_week, datetime.min.time(), UTC),
            )
            .group_by(AiCitationRun.id, AiCitationRun.finished_at)
            .order_by(AiCitationRun.finished_at)
        )
        return [
            CitationPoint(run_id, finished_at, answers, named, cited, competitor)
            for run_id, finished_at, answers, named, cited, competitor in result.all()
            if answers
        ]

    async def set_brand_terms(self, site_id: UUID, terms: list[str]) -> list[str]:
        """Replace the brand terms; an empty list goes back to the derived ones."""
        if self.context.role not in BRAND_TERM_ROLES:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_permissions")
        cleaned: list[str] = []
        for term in terms:
            flat = compact(term)
            if len(flat) < 3 or len(flat) > 60:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_brand_term")
            if flat not in cleaned:
                cleaned.append(flat)
        if len(cleaned) > MAX_BRAND_TERMS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="too_many_brand_terms")
        site = await self._site(site_id)
        previous = list(site.brand_terms or [])
        site.brand_terms = cleaned
        site.updated_at = datetime.now(UTC)
        metadata = {"site_id": str(site.id), "previous": previous, "brand_terms": cleaned}
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="site.brand_terms_updated",
                resource_type="site",
                resource_id=str(site.id),
                trace_id=self.context.trace_id,
                metadata_json=metadata,
                event_hash=stable_hash({**metadata, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        return brand_terms_for(site)

