"""Walking a date range of GA4 landing-page metrics, resumably.

The shape mirrors the Search Console sync, because the problem is the same: a
range of days, a page limit inside each day, and a checkpoint after every page
so a run that dies halfway resumes where it stopped rather than starting over.

One thing differs, and it removes a request per day. Search Console does not say
how many rows a day holds, so that sync asks for one more page and treats an
empty answer as the end. GA4 reports `rowCount`, so a day is finished when the
offset reaches it -- and a short page still ends the day, because a provider
that returns fewer rows than asked for has no more to give.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID

from app.analytics.client import ROW_LIMIT, LandingPageRow, LandingPageSource
from app.connectors.runtime import ZERO_WRITTEN, Written

MAX_PAGES_PER_DAY = 20


@dataclass(frozen=True, slots=True)
class AnalyticsCursor:
    day: date
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AnalyticsRecord:
    tenant_id: UUID
    site_id: UUID
    source_sync_id: UUID
    metric_date: date
    landing_page: str
    landing_page_hash: str
    channel_group: str
    device: str
    sessions: float
    engaged_sessions: float
    users: float
    views: float
    engagement_duration_seconds: float
    key_events: float


@dataclass(frozen=True, slots=True)
class AnalyticsSyncResult:
    days_completed: int
    rows_seen: int
    rows_upserted: int
    rows_new: int


class AnalyticsSink(Protocol):
    async def upsert_metrics(self, records: list[AnalyticsRecord]) -> Written: ...

    async def save_checkpoint(
        self,
        cursor: AnalyticsCursor,
        *,
        days_completed: int,
        rows_seen: int,
        rows_upserted: int,
        rows_new: int,
    ) -> None: ...


def analytics_record(
    row: LandingPageRow,
    *,
    tenant_id: UUID,
    site_id: UUID,
    sync_id: UUID,
    day: date,
) -> AnalyticsRecord:
    return AnalyticsRecord(
        tenant_id=tenant_id,
        site_id=site_id,
        source_sync_id=sync_id,
        metric_date=day,
        landing_page=row.landing_page,
        landing_page_hash=hashlib.sha256(row.landing_page.encode("utf-8")).hexdigest(),
        channel_group=row.channel_group,
        device=row.device,
        sessions=row.sessions,
        engaged_sessions=row.engaged_sessions,
        users=row.users,
        views=row.views,
        engagement_duration_seconds=row.engagement_duration_seconds,
        key_events=row.key_events,
    )


async def sync_landing_pages(
    source: LandingPageSource,
    sink: AnalyticsSink,
    *,
    tenant_id: UUID,
    site_id: UUID,
    sync_id: UUID,
    property_ref: str,
    range_start: date,
    range_end: date,
    cursor: AnalyticsCursor | None,
) -> AnalyticsSyncResult:
    if range_start > range_end:
        raise ValueError("invalid_sync_range")
    day = cursor.day if cursor else range_start
    offset = cursor.offset if cursor else 0
    if day < range_start or day > range_end or offset < 0 or offset % ROW_LIMIT:
        raise ValueError("invalid_sync_cursor")

    days_completed = 0
    rows_seen = 0
    written = ZERO_WRITTEN
    while day <= range_end:
        page_count = 0
        while True:
            if page_count >= MAX_PAGES_PER_DAY:
                raise RuntimeError("provider_daily_row_bound_exceeded")
            page = await source.query_day(property_ref, day, offset)
            page_count += 1
            if len(page.rows) > ROW_LIMIT:
                raise RuntimeError("provider_page_too_large")
            records = [
                analytics_record(
                    row, tenant_id=tenant_id, site_id=site_id, sync_id=sync_id, day=day
                )
                for row in page.rows
            ]
            rows_seen += len(records)
            written += await sink.upsert_metrics(records)
            offset += len(page.rows)
            if len(page.rows) < ROW_LIMIT or offset >= page.total_rows:
                next_day = day + timedelta(days=1)
                days_completed += 1
                await sink.save_checkpoint(
                    AnalyticsCursor(next_day, 0),
                    days_completed=days_completed,
                    rows_seen=rows_seen,
                    rows_upserted=written.total,
                    rows_new=written.new,
                )
                day = next_day
                offset = 0
                break
            await sink.save_checkpoint(
                AnalyticsCursor(day, offset),
                days_completed=days_completed,
                rows_seen=rows_seen,
                rows_upserted=written.total,
                rows_new=written.new,
            )
    return AnalyticsSyncResult(days_completed, rows_seen, written.total, written.new)
