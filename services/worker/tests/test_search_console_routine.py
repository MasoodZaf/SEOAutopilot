"""Keeping Search Console current without anyone making an API call.

Every search routine reads evidence something else was supposed to deposit:
`keyword_refresh` clusters `search_query`, the weekly report reads
`search_metric`. Nothing scheduled the sync that fills either, so on a site with
a working, authorised connector those routines skipped with "no evidence" and
measurement only existed for as long as somebody kept poking the API by hand.

What is worth pinning here is the seam. This routine queues work for a consumer
in another process, and the two agree only through an outbox row: get the event
type or the aggregate wrong and the routine reports success while nothing ever
runs.
"""

import json
import re
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.outbox import encode_event
from app.routines.runner import (
    SEARCH_CONSOLE_LAG_DAYS,
    SEARCH_CONSOLE_WINDOW_DAYS,
    _run_search_console_sync,
)

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
ROUTINE = UUID("019d0000-0000-7000-8000-000000000013")
CREATOR = UUID("019d0000-0000-7000-8000-000000000014")
CONNECTOR = UUID("019d0000-0000-7000-8000-000000000061")
SYNC = UUID("019d0000-0000-7000-8000-000000000071")
TODAY = date(2026, 9, 6)


class _Transaction:
    async def __aenter__(self) -> "None":
        return None

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeConnection:
    """Answers the three reads this routine makes, and records the writes."""

    def __init__(
        self,
        connector: dict[str, Any] | None,
        *,
        existing_sync: dict[str, Any] | None = None,
        created_by: UUID | None = CREATOR,
    ) -> None:
        self.connector = connector
        self.existing_sync = existing_sync
        self.created_by = created_by
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.statements.append((query, args))
        if "FROM connector\n" in query or "FROM connector " in query:
            return self.connector
        if "FROM connector_sync" in query:
            return self.existing_sync
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.statements.append((query, args))
        if "SELECT created_by FROM routine" in query:
            return self.created_by
        if "INSERT INTO connector_sync" in query:
            return SYNC
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.statements.append((query, args))
        return "OK"

    def written(self, needle: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [entry for entry in self.statements if needle in entry[0]]


def active_connector() -> dict[str, Any]:
    return {
        "id": CONNECTOR,
        "status": "active",
        "secret_ref": "db-envelope://019d0000-0000-7000-8000-0000000000aa",
    }


async def run(connection: FakeConnection, today: date = TODAY):
    return await _run_search_console_sync(connection, TENANT, SITE, ROUTINE, today)


@pytest.mark.asyncio
async def test_a_site_with_no_connector_is_skipped_not_failed() -> None:
    """Most sites will never have one. That is not an error condition."""
    connection = FakeConnection(None)
    status, summary, skip = await run(connection)

    assert (status, skip) == ("skipped", "search_console_connector_missing")
    assert summary == {}
    assert connection.written("INSERT INTO connector_sync") == []


@pytest.mark.asyncio
async def test_a_connector_awaiting_re_consent_says_so() -> None:
    """The reason a person can act on, rather than a generic failure.

    `reauthorization_required` means somebody has to click through Google.
    Reporting it as a plain skip would leave that sitting unnoticed while the
    routine kept quietly doing nothing every day.
    """
    connection = FakeConnection(
        {"id": CONNECTOR, "status": "reauthorization_required", "secret_ref": "db-envelope://x"}
    )
    status, summary, skip = await run(connection)

    assert (status, skip) == ("skipped", "search_console_connector_not_active")
    assert summary == {"connector_status": "reauthorization_required"}
    assert connection.written("INSERT INTO connector_sync") == []


@pytest.mark.asyncio
async def test_an_active_connector_queues_a_lagged_trailing_window() -> None:
    """Asking for yesterday reliably returns nothing.

    Search Console finalises a day's data a couple of days late and keeps
    revising it afterwards, so the window ends short of today and is wide
    enough that a late arrival lands on a later run.
    """
    connection = FakeConnection(active_connector())
    status, summary, skip = await run(connection)

    assert (status, skip) == ("completed", None)
    assert summary["sync_id"] == str(SYNC)
    assert summary["range_end"] == "2026-09-03"  # today - 3
    assert summary["range_start"] == "2026-08-28"  # a 7-day window

    inserted = connection.written("INSERT INTO connector_sync")
    assert len(inserted) == 1
    _query, args = inserted[0]
    tenant, connector, key, range_start, range_end, requested_by = args
    assert (tenant, connector) == (TENANT, CONNECTOR)
    assert (range_start, range_end) == (date(2026, 8, 28), date(2026, 9, 3))
    # The ingestion of somebody's search data still names a human who asked.
    assert requested_by == CREATOR
    assert key == f"routine:{ROUTINE}:2026-09-03"


@pytest.mark.asyncio
async def test_the_window_matches_the_constants_it_is_derived_from() -> None:
    connection = FakeConnection(active_connector())
    _status, summary, _skip = await run(connection)

    start = date.fromisoformat(str(summary["range_start"]))
    end = date.fromisoformat(str(summary["range_end"]))
    assert (TODAY - end).days == SEARCH_CONSOLE_LAG_DAYS
    assert (end - start).days + 1 == SEARCH_CONSOLE_WINDOW_DAYS


@pytest.mark.asyncio
async def test_the_queued_event_is_one_the_sync_consumer_will_act_on() -> None:
    """The seam. Two processes agree only through this row.

    The consumer filters on `type` and reads `tenant_id` and `aggregate_id`
    from the encoded stream fields. A wrong event type or aggregate here means
    the routine reports success and the sync never runs -- the exact silence
    this whole routine exists to end.
    """
    connection = FakeConnection(active_connector())
    await run(connection)

    events = connection.written("INSERT INTO outbox_event")
    assert len(events) == 1
    query, (tenant, aggregate_id, payload) = events[0]

    # Read the type and aggregate out of the statement rather than restating
    # them, so changing either in the handler fails here instead of passing a
    # test that was only ever checking its own literals.
    literals = re.findall(r"'([^']+)'", query.split("VALUES", 1)[1])
    event_type, aggregate_type = literals[0], literals[1]

    fields = encode_event(
        {
            "id": uuid4(),
            "tenant_id": tenant,
            "event_type": event_type,
            "event_version": 1,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "payload": json.loads(payload),
        }
    )
    # Exactly what run_gsc_consumer filters and reads off the message.
    assert fields["type"] == "connector.sync_requested"
    assert fields["aggregate_type"] == "connector_sync"
    assert UUID(fields["aggregate_id"]) == SYNC
    assert UUID(fields["tenant_id"]) == TENANT


@pytest.mark.asyncio
async def test_a_catch_up_run_for_a_served_slot_makes_no_second_sync() -> None:
    """A worker outage produces catch-up runs for slots already covered.

    Without a key derived from the window, each would queue another sync of the
    same days: the same rows re-fetched several times over, against a provider
    with a request quota.
    """
    connection = FakeConnection(
        active_connector(), existing_sync={"id": SYNC, "status": "completed"}
    )
    status, summary, skip = await run(connection)

    assert (status, skip) == ("skipped", "search_console_sync_already_requested")
    assert summary == {"sync_id": str(SYNC), "sync_status": "completed"}
    assert connection.written("INSERT INTO connector_sync") == []
    assert connection.written("INSERT INTO outbox_event") == []


@pytest.mark.asyncio
async def test_the_next_day_is_a_different_slot() -> None:
    """Deduplication must not become a permanent stop.

    A key that ignored the window would make the first run the only run.
    """
    first = FakeConnection(active_connector())
    await run(first, date(2026, 9, 6))
    second = FakeConnection(active_connector())
    await run(second, date(2026, 9, 7))

    keys = [
        connection.written("INSERT INTO connector_sync")[0][1][2]
        for connection in (first, second)
    ]
    assert keys[0] != keys[1]
    assert keys == [f"routine:{ROUTINE}:2026-09-03", f"routine:{ROUTINE}:2026-09-04"]


@pytest.mark.asyncio
async def test_a_routine_that_vanished_mid_run_queues_nothing() -> None:
    connection = FakeConnection(active_connector(), created_by=None)
    status, _summary, skip = await run(connection)

    assert (status, skip) == ("skipped", "routine_missing")
    assert connection.written("INSERT INTO connector_sync") == []
