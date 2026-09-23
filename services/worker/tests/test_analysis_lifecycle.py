"""What a second crawl does to the opportunities the first one opened.

`analyze_crawl` had never run against a database in a test: every analysis test
exercises the pure rules, and the integration suites insert findings and
opportunities by hand. So nothing checked the one promise a re-crawl makes --
that an issue fixed on the site leaves the queue -- or its limit, that a page
the crawl did not reach is left as it was.

Runs the real function on the real schema. Skipped without TEST_DATABASE_URL;
`make test-integration` provides one. It rebuilds the public schema, like the
API's integration suite.
"""

import json
import os
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from app.analysis import analyze_crawl

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is unset; run `make test-integration`",
    ),
]

MIGRATIONS = sorted((Path(__file__).resolve().parents[3] / "infra" / "migrations").glob("*.sql"))
TECHNICAL_V1 = UUID("019d0000-0000-7000-8000-000000000090")
THIN = "The page has fewer than 150 visible words."


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[asyncpg.Connection]:
    dsn = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    for migration in MIGRATIONS:
        await conn.execute(migration.read_text())
    try:
        yield conn
    finally:
        await conn.close()


class Site:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.tenant_id, self.site_id, self.actor_id = uuid4(), uuid4(), uuid4()
        self.pages: dict[str, UUID] = {}

    async def create(self) -> "Site":
        await self.conn.execute(
            "INSERT INTO tenant(id,slug,name,status) VALUES($1,$2,'life','active')",
            self.tenant_id,
            f"life-{self.tenant_id.hex[:8]}",
        )
        await self.conn.execute(
            "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,"
            "verified_at) VALUES($1,$2,'l','https://life.example','life.example','observe',"
            "'active',now())",
            self.site_id,
            self.tenant_id,
        )
        return self

    async def crawl(self, words_by_page: dict[str, int]) -> UUID:
        """A completed crawl that observed exactly these pages."""
        crawl_id = uuid4()
        await self.conn.execute(
            "INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status)"
            " VALUES($1,$2,$3,$4,'{}'::jsonb,'completed')",
            crawl_id,
            self.tenant_id,
            self.site_id,
            self.actor_id,
        )
        for name, words in words_by_page.items():
            url = f"https://life.example/{name}"
            if name not in self.pages:
                self.pages[name] = uuid4()
                await self.conn.execute(
                    "INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash)"
                    " VALUES($1,$2,$3,$4,$5)",
                    self.pages[name],
                    self.tenant_id,
                    self.site_id,
                    url,
                    sha256(url.encode()).hexdigest(),
                )
            await self.conn.execute(
                "INSERT INTO page_observation(id,tenant_id,page_id,crawl_job_id,http_status,"
                "final_url,title,meta_description,h1_json,word_count,content_hash,rendered)"
                " VALUES($1,$2,$3,$4,200,$5,$6,$7,$8::jsonb,$9,$10,false)",
                uuid4(),
                self.tenant_id,
                self.pages[name],
                crawl_id,
                url,
                f"A reasonable title for the {name} page",
                "A description long enough to sit inside the review range for snippets.",
                json.dumps([f"The {name} page"]),
                words,
                sha256(f"{crawl_id}{name}".encode()).hexdigest(),
            )
        return crawl_id

    async def thin_status(self, name: str) -> list[str]:
        rows = await self.conn.fetch(
            "SELECT status FROM opportunity WHERE tenant_id=$1 AND page_id=$2 AND title=$3",
            self.tenant_id,
            self.pages[name],
            THIN,
        )
        return sorted(row["status"] for row in rows)


async def test_a_fixed_page_leaves_the_queue_and_an_unread_page_does_not(connection) -> None:
    site = await Site(connection).create()

    first = await site.crawl({"fixed": 60, "unreached": 60})
    await analyze_crawl(connection, site.tenant_id, first)
    assert await site.thin_status("fixed") == ["open"]
    assert await site.thin_status("unreached") == ["open"]

    # The site fixes one page; the next crawl reads only that one.
    second = await site.crawl({"fixed": 600})
    await analyze_crawl(connection, site.tenant_id, second)

    assert await site.thin_status("fixed") == ["expired"]
    # Not seeing a page is not evidence it was fixed.
    assert await site.thin_status("unreached") == ["open"]


async def test_an_issue_still_present_stays_open_on_the_new_evidence(connection) -> None:
    site = await Site(connection).create()

    await analyze_crawl(connection, site.tenant_id, await site.crawl({"still": 60}))
    second = await site.crawl({"still": 60})
    await analyze_crawl(connection, site.tenant_id, second)

    row = await connection.fetchrow(
        "SELECT status,evidence_refs FROM opportunity WHERE page_id=$1 AND title=$2",
        site.pages["still"],
        THIN,
    )
    assert row is not None
    assert row["status"] == "open"
    assert json.loads(row["evidence_refs"])["crawl_id"] == str(second)


async def test_a_reread_page_closes_what_an_older_scoring_version_opened(connection) -> None:
    """An opportunity from a retired rule set used to stay open for ever."""
    site = await Site(connection).create()
    first = await site.crawl({"legacy": 600})
    legacy = uuid4()
    await connection.execute(
        "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,impact,confidence,urgency,"
        "effort,risk,score,scoring_version_id,evidence_refs,fingerprint) VALUES($1,$2,$3,$4,"
        "'A technical-v1 issue',0.5,0.5,0.5,0.5,'low',40,$5,$6::jsonb,$7)",
        legacy,
        site.tenant_id,
        site.site_id,
        site.pages["legacy"],
        TECHNICAL_V1,
        json.dumps({"crawl_id": str(first)}),
        sha256(legacy.bytes).hexdigest(),
    )

    await analyze_crawl(connection, site.tenant_id, await site.crawl({"legacy": 600}))

    status = await connection.fetchval("SELECT status FROM opportunity WHERE id=$1", legacy)
    assert status == "expired"
