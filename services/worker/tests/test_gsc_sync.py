from datetime import date
from uuid import UUID

import pytest
from app.connectors.runtime import Written
from app.gsc.client import ROW_LIMIT, SearchAnalyticsRow
from app.gsc.sync import SyncCursor, keyed_query_hash, sync_search_analytics

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
SYNC = UUID("019d0000-0000-7000-8000-000000000013")
HASH_KEY = b"x" * 32


def row(query: str = "confidential query") -> SearchAnalyticsRow:
    return SearchAnalyticsRow(query, "https://example.com/a", "usa", "mobile", 1, 10, 0.1, 8)


class FakeSource:
    """A source owns its credential, so the loop never sees one."""

    def __init__(self, replies: dict[tuple[date, int], list[SearchAnalyticsRow]]) -> None:
        self.replies = replies
        self.calls: list[tuple[date, int]] = []

    async def query_day(self, property_ref: str, day: date, start_row: int):
        self.calls.append((day, start_row))
        return self.replies.get((day, start_row), [])


class FakeSink:
    def __init__(self) -> None:
        self.keys: set[tuple[object, ...]] = set()
        self.records = []
        self.checkpoints: list[SyncCursor] = []

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
                record.query_hash,
                record.page_url_hash,
                record.country,
                record.device,
                record.search_type,
            )
            if key not in self.keys:
                self.keys.add(key)
                self.records.append(record)
                inserted += 1
        return Written(len(records), inserted)

    async def save_checkpoint(self, cursor, **counts):
        self.checkpoints.append(cursor)


@pytest.mark.asyncio
async def test_sync_is_daily_resumable_and_does_not_store_raw_query() -> None:
    first = date(2026, 8, 1)
    second = date(2026, 8, 2)
    provider = FakeSource({(first, 0): [row()], (second, 0): [row("another query")]})
    sink = FakeSink()
    result = await sync_search_analytics(
        provider,
        sink,
        tenant_id=TENANT,
        site_id=SITE,
        sync_id=SYNC,
        property_ref="sc-domain:example.com",
        range_start=first,
        range_end=second,
        cursor=None,
        query_hash_key=HASH_KEY,
    )
    assert result.days_completed == 2
    assert result.rows_seen == 2
    assert result.rows_upserted == 2
    assert provider.calls == [(first, 0), (first, ROW_LIMIT), (second, 0), (second, ROW_LIMIT)]
    assert sink.records[0].query_hash == keyed_query_hash("confidential query", HASH_KEY)
    assert "confidential query" not in repr(sink.records)
    assert "confidential query" not in str(sink.records)
    # The term is carried in memory for the envelope sink only, and is
    # excluded from the record's repr so a logged record cannot leak it.
    assert sink.records[0].query_text == "confidential query"
    assert sink.checkpoints[-1] == SyncCursor(date(2026, 8, 3), 0)


@pytest.mark.asyncio
async def test_replay_after_checkpoint_loss_is_idempotent() -> None:
    day = date(2026, 8, 1)
    provider = FakeSource({(day, 0): [row()]})
    sink = FakeSink()
    first = await sync_search_analytics(
        provider,
        sink,
        tenant_id=TENANT,
        site_id=SITE,
        sync_id=SYNC,
        property_ref="sc-domain:example.com",
        range_start=day,
        range_end=day,
        cursor=SyncCursor(day, 0),
        query_hash_key=HASH_KEY,
    )
    second = await sync_search_analytics(
        provider,
        sink,
        tenant_id=TENANT,
        site_id=SITE,
        sync_id=SYNC,
        property_ref="sc-domain:example.com",
        range_start=day,
        range_end=day,
        cursor=SyncCursor(day, 0),
        query_hash_key=HASH_KEY,
    )
    assert first.rows_upserted == 1
    assert first.rows_new == 1
    assert second.rows_seen == 1
    # The replay writes the row again and creates nothing. Both numbers are
    # part of the claim: `rows_upserted == 1` says the sync did its work, and
    # `rows_new == 0` says the work did not duplicate anything. Asserting only
    # that a replay "upserted 0" described a working sync as an idle one.
    assert second.rows_upserted == 1
    assert second.rows_new == 0
    assert len(sink.records) == 1


def test_query_hash_requires_a_separate_strong_key() -> None:
    with pytest.raises(ValueError, match="search_query_hash_key_too_short"):
        keyed_query_hash("query", b"short")
