"""Re-running the same keyword window must not erase the briefs built from it.

Briefs hang off keyword clusters with ON DELETE CASCADE. The daily
keyword_refresh re-ran the same window and replaced its clusters wholesale, so
every morning's briefs were deleted within hours: TheCalcHive's routine
reported five briefs written each day and the site had none.
"""

import hashlib
import os
import secrets
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from app.briefs.generate import generate_briefs
from app.keywords import analysis
from app.keywords.cluster import QueryMetrics
from app.keywords.secrets import seal_query

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is unset; run `make test-integration`",
    ),
]
MIGRATIONS = sorted((Path(__file__).resolve().parents[3] / "infra" / "migrations").glob("*.sql"))
KEY = secrets.token_bytes(32)
TERMS = [
    "emi calculator", "emi calculator online", "home loan emi calculator",
    "bmi calculator", "bmi calculator for women", "bmi calculator kg",
]


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


async def seed(conn: asyncpg.Connection) -> tuple[UUID, UUID]:
    tenant, site = uuid4(), uuid4()
    await conn.execute("INSERT INTO tenant(id,slug,name,status) VALUES($1,$2,'k','active')", tenant, f"k-{tenant.hex[:8]}")
    await conn.execute(
        "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
        " VALUES($1,$2,'Calc','https://calc.example','calc.example','observe','active',now())",
        site, tenant,
    )
    for term in TERMS:
        ciphertext, nonce, aad_hash = seal_query(KEY, tenant, site, "v1", term)
        await conn.execute(
            "INSERT INTO search_query(tenant_id,site_id,query_hash,ciphertext,nonce,aad_hash,key_version,term_length,token_count,is_question)"
            " VALUES($1,$2,$3,$4,$5,$6,'v1',$7,$8,false)",
            tenant, site, hashlib.sha256(term.encode()).hexdigest(), ciphertext, nonce, aad_hash,
            len(term), len(term.split()),
        )
    return tenant, site


def metrics(terms: list[str]) -> list[QueryMetrics]:
    return [
        QueryMetrics(hashlib.sha256(term.encode()).hexdigest(), term, 1.0, 40.0, 60.0)
        for term in terms
    ]


async def test_a_rerun_of_the_same_window_keeps_its_clusters_and_their_briefs(connection, monkeypatch):
    tenant, site = await seed(connection)
    evidence = metrics(TERMS)

    async def load(*_args, **_kwargs):
        return evidence

    monkeypatch.setattr(analysis, "load_query_metrics", load)
    window = (date(2026, 10, 1), date(2026, 10, 28))

    first = await analysis.run_keyword_analysis(connection, tenant, site, *window, KEY)
    run_id = UUID(first["analysis_run_id"])
    summary = await generate_briefs(connection, tenant, site, run_id)
    assert summary["briefs_written"] >= 1
    before = await connection.fetch("SELECT id, keyword_cluster_id FROM content_brief ORDER BY id")

    again = await analysis.run_keyword_analysis(connection, tenant, site, *window, KEY)
    assert again["analysis_run_id"] == first["analysis_run_id"]
    after = await connection.fetch("SELECT id, keyword_cluster_id FROM content_brief ORDER BY id")
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert await connection.fetchval("SELECT count(*) FROM keyword_cluster_member") == len(TERMS)


async def test_a_cluster_the_new_evidence_no_longer_forms_is_removed(connection, monkeypatch):
    tenant, site = await seed(connection)
    evidence = metrics(TERMS)

    async def load(*_args, **_kwargs):
        return evidence

    monkeypatch.setattr(analysis, "load_query_metrics", load)
    window = (date(2026, 10, 1), date(2026, 10, 28))
    first = await analysis.run_keyword_analysis(connection, tenant, site, *window, KEY)
    clusters_before = first["clusters_built"]
    assert clusters_before >= 2

    evidence = metrics(TERMS[:3])
    second = await analysis.run_keyword_analysis(connection, tenant, site, *window, KEY)
    assert second["clusters_built"] < clusters_before
    assert await connection.fetchval("SELECT count(*) FROM keyword_cluster") == second["clusters_built"]
