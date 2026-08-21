import asyncio
import os
from typing import cast

import asyncpg
import redis.asyncio as redis

from app.outbox import DatabaseConnection, StreamClient, dispatch_batch


async def run_once() -> None:
    database_url = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    connection = await asyncpg.connect(database_url)
    streams = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        count = await dispatch_batch(
            cast(DatabaseConnection, connection), cast(StreamClient, streams)
        )
        print(f"dispatched={count}")
    finally:
        await streams.aclose()
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run_once())
