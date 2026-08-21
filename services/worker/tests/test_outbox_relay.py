from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from app.outbox_relay import MockEventDispatcher, process_outbox_batch


class MockConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.executed_queries: list[tuple[str, tuple[object, ...]]] = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetch(self, query: str, limit: int, max_attempts: int) -> list[dict[str, object]]:
        return self.rows[:limit]

    async def execute(self, query: str, *args: object) -> str:
        self.executed_queries.append((query, args))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_process_outbox_batch_dispatches_and_marks_published() -> None:
    row_1 = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "event_type": "proposal.created.v1",
        "event_version": 1,
        "aggregate_type": "proposal",
        "aggregate_id": uuid4(),
        "payload": {"proposal_id": "p-1"},
        "attempts": 0,
    }
    row_2 = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "event_type": "proposal.approved.v1",
        "event_version": 1,
        "aggregate_type": "proposal",
        "aggregate_id": uuid4(),
        "payload": {"proposal_id": "p-2"},
        "attempts": 0,
    }

    conn = MockConnection([row_1, row_2])
    dispatcher = MockEventDispatcher()

    count = await process_outbox_batch(
        connection=conn,  # type: ignore[arg-type]
        dispatcher=dispatcher,
        batch_size=10,
    )

    assert count == 2
    assert len(dispatcher.dispatched_events) == 2
    assert len(conn.executed_queries) == 2
    assert "SET published_at=now()" in conn.executed_queries[0][0]
