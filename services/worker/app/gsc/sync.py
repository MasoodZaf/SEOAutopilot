from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID

from app.connectors.runtime import ZERO_WRITTEN, Written
from app.gsc.client import ROW_LIMIT, SearchAnalyticsRow, SearchAnalyticsSource

MAX_PAGES_PER_DAY = 10


@dataclass(frozen=True, slots=True)
class SyncCursor:
    day: date
    start_row: int = 0


@dataclass(frozen=True, slots=True)
class MetricRecord:
    tenant_id: UUID
    site_id: UUID
    source_sync_id: UUID
    metric_date: date
    query_hash: str
    page_url: str
    page_url_hash: str
    country: str
    device: str
    search_type: str
    clicks: float
    impressions: float
    ctr: float
    position: float
    # In-memory only, and excluded from repr so an accidentally logged record
    # cannot leak it. The sink seals it into the search_query envelope; it is
    # never written to search_metric, a log line, or an error message.
    query_text: str = field(repr=False, default="")


@dataclass(frozen=True, slots=True)
class SyncResult:
    days_completed: int
    rows_seen: int
    rows_upserted: int
    rows_new: int


class MetricSink(Protocol):
    async def upsert_metrics(self, records: list[MetricRecord]) -> Written: ...

    async def save_checkpoint(
        self,
        cursor: SyncCursor,
        *,
        days_completed: int,
        rows_seen: int,
        rows_upserted: int,
        rows_new: int,
    ) -> None: ...


def keyed_query_hash(query: str, key: bytes) -> str:
    if len(key) < 32:
        raise ValueError("search_query_hash_key_too_short")
    return hmac.new(key, query.encode("utf-8"), hashlib.sha256).hexdigest()


def metric_record(
    row: SearchAnalyticsRow,
    *,
    tenant_id: UUID,
    site_id: UUID,
    sync_id: UUID,
    day: date,
    query_hash_key: bytes,
) -> MetricRecord:
    return MetricRecord(
        tenant_id=tenant_id,
        site_id=site_id,
        source_sync_id=sync_id,
        metric_date=day,
        query_hash=keyed_query_hash(row.query, query_hash_key),
        query_text=row.query,
        page_url=row.page_url,
        page_url_hash=hashlib.sha256(row.page_url.encode("utf-8")).hexdigest(),
        country=row.country,
        device=row.device,
        search_type="web",
        clicks=row.clicks,
        impressions=row.impressions,
        ctr=row.ctr,
        position=row.position,
    )


async def sync_search_analytics(
    source: SearchAnalyticsSource,
    sink: MetricSink,
    *,
    tenant_id: UUID,
    site_id: UUID,
    sync_id: UUID,
    property_ref: str,
    range_start: date,
    range_end: date,
    cursor: SyncCursor | None,
    query_hash_key: bytes,
) -> SyncResult:
    if range_start > range_end:
        raise ValueError("invalid_sync_range")
    day = cursor.day if cursor else range_start
    start_row = cursor.start_row if cursor else 0
    if day < range_start or day > range_end or start_row < 0 or start_row % ROW_LIMIT:
        raise ValueError("invalid_sync_cursor")

    days_completed = 0
    rows_seen = 0
    written = ZERO_WRITTEN
    while day <= range_end:
        page_count = 0
        while True:
            if page_count >= MAX_PAGES_PER_DAY:
                raise RuntimeError("provider_daily_row_bound_exceeded")
            rows = await source.query_day(property_ref, day, start_row)
            page_count += 1
            if not rows:
                next_day = day + timedelta(days=1)
                days_completed += 1
                await sink.save_checkpoint(
                    SyncCursor(next_day, 0),
                    days_completed=days_completed,
                    rows_seen=rows_seen,
                    rows_upserted=written.total,
                    rows_new=written.new,
                )
                day = next_day
                start_row = 0
                break
            if len(rows) > ROW_LIMIT:
                raise RuntimeError("provider_page_too_large")
            records = [
                metric_record(
                    row,
                    tenant_id=tenant_id,
                    site_id=site_id,
                    sync_id=sync_id,
                    day=day,
                    query_hash_key=query_hash_key,
                )
                for row in rows
            ]
            rows_seen += len(records)
            written += await sink.upsert_metrics(records)
            start_row += ROW_LIMIT
            await sink.save_checkpoint(
                SyncCursor(day, start_row),
                days_completed=days_completed,
                rows_seen=rows_seen,
                rows_upserted=written.total,
                rows_new=written.new,
            )
            if len(rows) < ROW_LIMIT:
                # Still issue the documented empty-page request. It makes completion explicit
                # and keeps the resume checkpoint independent of a provider's short page.
                continue
    return SyncResult(days_completed, rows_seen, written.total, written.new)

