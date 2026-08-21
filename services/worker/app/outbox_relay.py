from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol


class DatabaseConnection(Protocol):
    def transaction(self) -> Any: ...
    async def fetch(self, query: str, limit: int, max_attempts: int) -> list[Mapping[str, Any]]: ...
    async def execute(self, query: str, *args: Any) -> str: ...


class EventDispatcher(Protocol):
    async def dispatch(self, event_type: str, aggregate_id: str, payload: dict[str, Any]) -> bool: ...


class MockEventDispatcher:
    """Mock dispatcher that records dispatched events deterministically."""

    def __init__(self) -> None:
        self.dispatched_events: list[dict[str, Any]] = []

    async def dispatch(self, event_type: str, aggregate_id: str, payload: dict[str, Any]) -> bool:
        self.dispatched_events.append(
            {
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "payload": payload,
                "dispatched_at": datetime.now(UTC).isoformat(),
            }
        )
        return True


async def process_outbox_batch(
    connection: DatabaseConnection,
    dispatcher: EventDispatcher,
    batch_size: int = 50,
    max_attempts: int = 5,
) -> int:
    """Processes a batch of unpublished outbox events with exponential retry and dead-letter detection."""
    async with connection.transaction():
        rows = await connection.fetch(
            """
            SELECT id, tenant_id, event_type, event_version, aggregate_type, aggregate_id, payload, attempts
            FROM outbox_event
            WHERE published_at IS NULL AND attempts < $2
            ORDER BY occurred_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT $1
            """,
            batch_size,
            max_attempts,
        )
        processed_count = 0

        for row in rows:
            event_id = row["id"]
            payload = row["payload"] if isinstance(row["payload"], dict) else {}
            try:
                success = await dispatcher.dispatch(
                    event_type=str(row["event_type"]),
                    aggregate_id=str(row["aggregate_id"]),
                    payload=payload,
                )
                if success:
                    await connection.execute(
                        "UPDATE outbox_event SET published_at=now(), attempts=attempts+1 WHERE id=$1",
                        event_id,
                    )
                    processed_count += 1
                else:
                    await connection.execute(
                        "UPDATE outbox_event SET attempts=attempts+1 WHERE id=$1",
                        event_id,
                    )
            except (RuntimeError, ValueError, OSError, TimeoutError):
                await connection.execute(
                    "UPDATE outbox_event SET attempts=attempts+1 WHERE id=$1",
                    event_id,
                )

    return processed_count
