import json
from collections.abc import Mapping
from typing import Any, Protocol


class DatabaseConnection(Protocol):
    def transaction(self) -> Any: ...
    async def fetch(self, query: str, limit: int) -> list[Mapping[str, Any]]: ...
    async def execute(self, query: str, event_id: Any) -> str: ...


class StreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


def encode_event(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "event_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "type": str(row["event_type"]),
        "version": str(row["event_version"]),
        "aggregate_type": str(row["aggregate_type"]),
        "aggregate_id": str(row["aggregate_id"]),
        "payload": json.dumps(row["payload"], sort_keys=True, separators=(",", ":")),
    }


async def dispatch_batch(
    connection: DatabaseConnection,
    streams: StreamClient,
    *,
    limit: int = 100,
) -> int:
    async with connection.transaction():
        rows = await connection.fetch(
            """
            SELECT id,tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload
            FROM outbox_event
            WHERE published_at IS NULL
            ORDER BY occurred_at,id
            FOR UPDATE SKIP LOCKED
            LIMIT $1
            """,
            limit,
        )
        for row in rows:
            await streams.xadd("seo-autopilot:events", encode_event(row))
            await connection.execute(
                "UPDATE outbox_event SET published_at=now(),attempts=attempts+1 WHERE id=$1",
                row["id"],
            )
    return len(rows)
