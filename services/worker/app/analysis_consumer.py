import logging
from collections.abc import Mapping
from typing import Any, Protocol, cast
from uuid import UUID

from redis.exceptions import ResponseError

from app.analysis import AnalysisConnection, analyze_crawl

STREAM = "seo-autopilot:events"
GROUP = "analysis"
logger = logging.getLogger(__name__)


class AnalysisPool(Protocol):
    def acquire(self) -> Any: ...


class AnalysisStream(Protocol):
    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> bool: ...
    async def xautoclaim(self, name: str, groupname: str, consumername: str, **kwargs: Any) -> Any: ...
    async def xreadgroup(self, groupname: str, consumername: str, streams: dict[str, str], **kwargs: Any) -> Any: ...
    async def xack(self, name: str, groupname: str, *ids: str) -> int: ...


def reclaimed_messages(reply: object) -> list[tuple[str, Mapping[str, str]]]:
    if not isinstance(reply, (list, tuple)) or len(reply) < 2:
        return []
    messages = reply[1]
    if not isinstance(messages, list):
        return []
    return [
        (message_id, fields)
        for message_id, fields in messages
        if isinstance(message_id, str) and isinstance(fields, Mapping)
    ]


def new_messages(reply: object) -> list[tuple[str, Mapping[str, str]]]:
    if not isinstance(reply, list):
        return []
    result: list[tuple[str, Mapping[str, str]]] = []
    for stream in reply:
        if not isinstance(stream, (list, tuple)) or len(stream) != 2:
            continue
        messages = stream[1]
        if not isinstance(messages, list):
            continue
        result.extend(
            (message_id, fields)
            for message_id, fields in messages
            if isinstance(message_id, str) and isinstance(fields, Mapping)
        )
    return result


async def run_analysis_consumer(
    pool: AnalysisPool,
    streams: AnalysisStream,
    consumer: str,
) -> None:
    try:
        await streams.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise

    while True:
        reclaimed = reclaimed_messages(
            await streams.xautoclaim(
                STREAM,
                GROUP,
                consumer,
                min_idle_time=60_000,
                start_id="0-0",
                count=1,
            )
        )
        messages = reclaimed or new_messages(
            await streams.xreadgroup(
                GROUP,
                consumer,
                {STREAM: ">"},
                count=1,
                block=5_000,
            )
        )
        for message_id, fields in messages:
            if fields.get("type") != "crawl.completed":
                await streams.xack(STREAM, GROUP, message_id)
                continue
            try:
                tenant_id = UUID(fields["tenant_id"])
                crawl_id = UUID(fields["aggregate_id"])
                async with pool.acquire() as connection:
                    # analyze_crawl sets the scope itself, inside its transaction.
                    await analyze_crawl(cast(AnalysisConnection, connection), tenant_id, crawl_id)
                await streams.xack(STREAM, GROUP, message_id)
            except (KeyError, ValueError):
                logger.exception("invalid analysis event", extra={"event_id": message_id})
                await streams.xack(STREAM, GROUP, message_id)
            except Exception:
                logger.exception("crawl analysis failed", extra={"event_id": message_id})
