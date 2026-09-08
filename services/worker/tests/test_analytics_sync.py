"""Walking a range of days, and resuming when a run dies partway.

The property that matters is that no row is lost and none is counted twice
across a resume, because a backfill that has to start over is a backfill that
never finishes. The GA4 loop can also finish a day without an extra request,
which the Search Console loop cannot -- `rowCount` says when a day is done -- so
that saving is pinned too, since losing it would be invisible.
"""

from datetime import date
from uuid import UUID

import pytest
from app.analytics.client import ROW_LIMIT, LandingPagePage, LandingPageRow
from app.analytics.sync import AnalyticsCursor, sync_landing_pages
from app.connectors.runtime import Written

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
SYNC = UUID("019d0000-0000-7000-8000-000000000013")


def row(landing: str = "/emi-calculator") -> LandingPageRow:
    return LandingPageRow(landing, "Organic Search", "mobile", 4, 3, 4, 5, 120, 1)


class FakeSource:
    """A source owns its credential, so the loop never sees one."""

    def __init__(self, replies: dict[tuple[date, int], LandingPagePage]) -> None:
        self.replies = replies
        self.calls: list[tuple[date, int]] = []

    async def query_day(self, property_ref: str, day: date, offset: int) -> LandingPagePage:
        self.calls.append((day, offset))
        return self.replies.get((day, offset), LandingPagePage((), 0))


class FakeSink:
    def __init__(self) -> None:
        self.keys: set[tuple[object, ...]] = set()
        self.records: list = []
        self.checkpoints: list[AnalyticsCursor] = []

    async def upsert_metrics(self, records):
        # Mirrors the real sink: every row is written, only an unseen key is
        # new. Returning inserts alone was what made a healthy re-sync report
        # that it had stored nothing.
        inserted = 0
        for record in records:
            key = (
                record.tenant_id,
                record.site_id,
                record.metric_date,
                record.landing_page_hash,
                record.channel_group,
                record.device,
            )
            if key not in self.keys:
                self.keys.add(key)
                self.records.append(record)
                inserted += 1
        return Written(len(records), inserted)

    async def save_checkpoint(self, cursor, **counts):
        self.checkpoints.append(cursor)


async def run(source: FakeSource, sink: FakeSink, *, start, end, cursor=None):
    return await sync_landing_pages(
        source,
        sink,
        tenant_id=TENANT,
        site_id=SITE,
        sync_id=SYNC,
        property_ref="properties/123",
        range_start=start,
        range_end=end,
        cursor=cursor,
    )


@pytest.mark.asyncio
async def test_a_short_page_ends_the_day_without_another_request() -> None:
    """Search Console needs a trailing empty request; GA4 reports the total.

    Two days of one page each is two requests, not four.
    """
    first, second = date(2026, 9, 1), date(2026, 9, 2)
    source = FakeSource(
        {
            (first, 0): LandingPagePage((row("/a"),), 1),
            (second, 0): LandingPagePage((row("/b"),), 1),
        }
    )
    sink = FakeSink()

    result = await run(source, sink, start=first, end=second)

    assert source.calls == [(first, 0), (second, 0)]
    assert result.days_completed == 2
    assert result.rows_seen == 2
    assert result.rows_upserted == 2


@pytest.mark.asyncio
async def test_a_full_page_is_followed_and_the_total_ends_the_day() -> None:
    day = date(2026, 9, 1)
    full = tuple(row(f"/page-{index}") for index in range(ROW_LIMIT))
    source = FakeSource(
        {
            (day, 0): LandingPagePage(full, ROW_LIMIT + 2),
            (day, ROW_LIMIT): LandingPagePage((row("/last-1"), row("/last-2")), ROW_LIMIT + 2),
        }
    )
    sink = FakeSink()

    result = await run(source, sink, start=day, end=day)

    assert source.calls == [(day, 0), (day, ROW_LIMIT)]
    assert result.rows_seen == ROW_LIMIT + 2
    assert result.days_completed == 1


@pytest.mark.asyncio
async def test_a_resumed_sync_repeats_no_row_and_loses_none() -> None:
    """The checkpoint is the whole reason a long backfill can finish."""
    day = date(2026, 9, 1)
    full = tuple(row(f"/page-{index}") for index in range(ROW_LIMIT))
    tail = (row("/tail"),)
    replies = {
        (day, 0): LandingPagePage(full, ROW_LIMIT + 1),
        (day, ROW_LIMIT): LandingPagePage(tail, ROW_LIMIT + 1),
    }

    interrupted = FakeSink()
    await run(FakeSource({(day, 0): replies[(day, 0)]}), interrupted, start=day, end=day)
    resume = interrupted.checkpoints[0]
    assert resume == AnalyticsCursor(day, ROW_LIMIT)

    resumed_source = FakeSource(replies)
    resumed = FakeSink()
    result = await run(resumed_source, resumed, start=day, end=day, cursor=resume)

    assert resumed_source.calls == [(day, ROW_LIMIT)]
    assert result.rows_seen == 1
    assert {record.landing_page for record in resumed.records} == {"/tail"}


@pytest.mark.asyncio
async def test_a_re_synced_day_overwrites_rather_than_doubling() -> None:
    """Overlapping windows are deliberate, so this must not accumulate.

    The routine re-syncs the last week every run so late-arriving data corrects
    itself. That is only safe because a row is keyed, not appended.
    """
    day = date(2026, 9, 1)
    source = FakeSource({(day, 0): LandingPagePage((row("/a"), row("/a")), 2)})
    sink = FakeSink()

    result = await run(source, sink, start=day, end=day)

    assert result.rows_seen == 2
    # Two rows read, two written, one of them new: the second collapses onto
    # the first's key rather than being appended.
    assert result.rows_upserted == 2
    assert result.rows_new == 1
    assert len(sink.records) == 1


@pytest.mark.asyncio
async def test_a_day_with_no_traffic_completes_rather_than_stalling() -> None:
    day = date(2026, 9, 1)
    sink = FakeSink()
    result = await run(FakeSource({}), sink, start=day, end=day)
    assert result.days_completed == 1
    assert result.rows_seen == 0


@pytest.mark.asyncio
async def test_an_impossible_range_or_cursor_is_refused() -> None:
    day = date(2026, 9, 1)
    with pytest.raises(ValueError, match="invalid_sync_range"):
        await run(FakeSource({}), FakeSink(), start=day, end=date(2026, 8, 1))
    for cursor in (
        AnalyticsCursor(date(2026, 8, 1), 0),
        AnalyticsCursor(day, -1),
        AnalyticsCursor(day, 7),
    ):
        with pytest.raises(ValueError, match="invalid_sync_cursor"):
            await run(FakeSource({}), FakeSink(), start=day, end=day, cursor=cursor)


@pytest.mark.asyncio
async def test_a_provider_that_never_ends_a_day_is_stopped() -> None:
    """A total that keeps outrunning the offset would page for ever."""
    day = date(2026, 9, 1)
    full = tuple(row(f"/page-{index}") for index in range(ROW_LIMIT))

    class Endless:
        async def query_day(self, property_ref, requested_day, offset):
            return LandingPagePage(full, 10_000_000)

    with pytest.raises(RuntimeError, match="provider_daily_row_bound_exceeded"):
        await run(Endless(), FakeSink(), start=day, end=day)  # type: ignore[arg-type]
