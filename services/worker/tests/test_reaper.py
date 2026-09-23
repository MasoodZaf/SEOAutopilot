import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from app.content_drafts.consumer import EVENT as CONTENT_DRAFT_EVENT
from app.reaper import LEASED_WORK, fail_sql, requeue_sql, sweep_once


class FakeConnection:
    """Records every statement and answers each with a canned row list."""

    def __init__(self, rows_per_query: int = 0) -> None:
        self.rows_per_query = rows_per_query
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((query, args))
        return [{"id": uuid4()} for _ in range(self.rows_per_query)]

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        return "UPDATE 0"


def test_every_leased_table_has_a_delivery_event() -> None:
    # A table the reaper can requeue but whose event no consumer listens for
    # would queue work that never runs again -- worse than leaving it running,
    # because it also looks fixed.
    listened_for = {
        "crawl.requested",
        "performance.requested",
        "routine.run.queued.v1",
        "connector.sync_requested",
        CONTENT_DRAFT_EVENT,
    }
    for work in LEASED_WORK:
        assert work.event_type in listened_for, work.table


def test_notification_delivery_is_not_swept() -> None:
    # Its dispatcher already re-reads the table every tick, so sweeping it here
    # would race two writers against the same rows for no gain.
    assert "notification_delivery" not in {work.table for work in LEASED_WORK}


def test_requeue_and_fail_select_disjoint_rows() -> None:
    # The two statements run in the same transaction against the same table, so
    # if their predicates overlapped a row could be queued and failed at once.
    for work in LEASED_WORK:
        if work.attempts_column is None:
            continue
        assert f"{work.attempts_column} < $2" in requeue_sql(work)
        assert f"{work.attempts_column} >= $2" in fail_sql(work)


def test_only_expired_leases_are_touched() -> None:
    for work in LEASED_WORK:
        statements = [fail_sql(work)]
        if work.attempts_column is not None:
            statements.append(requeue_sql(work))
        for statement in statements:
            assert "status='running'" in statement
            assert "lease_until IS NOT NULL" in statement
            assert "lease_until < now() - ($1 * interval '1 second')" in statement


def test_requeue_publishes_the_event_in_the_same_statement() -> None:
    # Two statements could be interrupted between the update and the insert,
    # leaving a queued row with nothing to deliver it -- the same orphan under
    # a different status.
    for work in LEASED_WORK:
        if work.attempts_column is None:
            continue
        statement = requeue_sql(work)
        assert "WITH abandoned AS (" in statement
        assert "INSERT INTO outbox_event" in statement


@pytest.mark.asyncio
async def test_sweep_counts_both_outcomes_and_binds_the_grace_period() -> None:
    connection = FakeConnection(rows_per_query=2)
    counts = await sweep_once(connection, grace_seconds=42)  # type: ignore[arg-type]

    tracked = [work for work in LEASED_WORK if work.attempts_column is not None]
    assert counts["requeued"] == 2 * len(tracked)
    assert counts["failed"] == 2 * len(LEASED_WORK)
    assert all(args[0] == 42 for _, args in connection.calls)


@pytest.mark.asyncio
async def test_a_table_without_attempts_is_failed_not_requeued() -> None:
    connection = FakeConnection(rows_per_query=1)
    await sweep_once(connection)  # type: ignore[arg-type]

    untracked = [work for work in LEASED_WORK if work.attempts_column is None]
    assert untracked, "the test is meaningless if every table tracks attempts"
    for work in untracked:
        emitted = [query for query, _ in connection.calls if work.table in query]
        assert emitted, work.table
        assert not any("INSERT INTO outbox_event" in query for query in emitted)
        # asyncpg rejects an argument the statement does not bind.
        for query, args in connection.calls:
            if work.table in query:
                assert len(args) == 1


@pytest.mark.asyncio
async def test_requeue_payload_names_the_reason() -> None:
    connection = FakeConnection(rows_per_query=1)
    await sweep_once(connection)  # type: ignore[arg-type]

    payloads = [
        args[4] for query, args in connection.calls if "INSERT INTO outbox_event" in query
    ]
    assert payloads
    assert all(json.loads(payload) == {"reason": "lease_expired"} for payload in payloads)
