import asyncio
import json
import os
from typing import cast
from uuid import UUID

import asyncpg

from app.analysis import AnalysisConnection, analyze_crawl


async def run() -> None:
    database_url = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    connection = await asyncpg.connect(database_url)
    try:
        result = await analyze_crawl(
            cast(AnalysisConnection, connection),
            UUID(os.environ["TENANT_ID"]),
            UUID(os.environ["CRAWL_ID"]),
        )
        print(
            json.dumps(
                {
                    "run_id": str(result.run_id),
                    "score_count": result.score_count,
                    "finding_count": result.finding_count,
                    "opportunity_count": result.opportunity_count,
                    "already_completed": result.already_completed,
                },
                sort_keys=True,
            )
        )
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run())
