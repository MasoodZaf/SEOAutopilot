"""A content draft, from queued to ready, against PostgreSQL.

The model is a fake: what is under test is everything around it -- the claim,
the workspace's own key and nothing else, the budget, the decrypted search
terms reaching the prompt, and the flags a reviewer must clear.
"""

import hashlib
import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.connectors.tenant_clients import credential_aad
from app.content_drafts.consumer import process_draft
from app.content_drafts.model import DraftModelError, ModelAnswer
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


class FakeModel:
    def __init__(self, error: DraftModelError | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def draft(self, *, api_key: str, model: str, system: str, user: str) -> ModelAnswer:
        self.calls.append({"api_key": api_key, "model": model, "user": user})
        if self.error:
            raise self.error
        body = (
            "EMI is the fixed monthly payment on a loan. [Try the calculator](/emi-calculator).\n\n"
            "## How is EMI calculated?\n\nThe formula uses the principal, rate and tenure. "
            "A 12% annual rate is 1% a month.\n\n## Worked example\n\nWork it through step by step."
        )
        return ModelAnswer(
            answer={
                "title": "How EMI is calculated",
                "slug": "how-emi-is-calculated",
                "meta_description": "The EMI formula explained with a worked example.",
                "body_markdown": body,
                "claims": [{"text": "The formula uses the principal, rate and tenure.", "verify": "Check."}],
                "internal_links": ["/emi-calculator"],
            },
            model=model,
            input_tokens=2_000,
            output_tokens=3_000,
        )


async def _seed(conn: asyncpg.Connection, *, provider: str = "anthropic", key: str | None = "sk-ant-test-key-000000") -> dict[str, UUID]:
    ids = {name: uuid4() for name in ("tenant", "site", "run", "cluster", "brief", "crawl", "page", "draft", "actor")}
    t, s = ids["tenant"], ids["site"]
    await conn.execute("INSERT INTO tenant(id,slug,name,status) VALUES($1,$2,'d','active')", t, f"d-{t.hex[:8]}")
    await conn.execute(
        "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
        " VALUES($1,$2,'Calc','https://calc.example','calc.example','observe','active',now())",
        s, t,
    )
    await conn.execute(
        "INSERT INTO keyword_analysis_run(id,tenant_id,site_id,algorithm_version,window_start,window_end,content_hash)"
        " VALUES($1,$2,$3,'t','2026-09-01','2026-09-20',$4)",
        ids["run"], t, s, "a" * 64,
    )
    await conn.execute(
        "INSERT INTO keyword_cluster(id,tenant_id,site_id,analysis_run_id,label,cluster_key,intent,member_count,opportunity_score)"
        " VALUES($1,$2,$3,$4,'emi formula','emi formula','informational',1,30)",
        ids["cluster"], t, s, ids["run"],
    )
    term = "how is emi calculated"
    qhash = hashlib.sha256(term.encode()).hexdigest()
    ciphertext, nonce, aad_hash = seal_query(KEY, t, s, "v1", term)
    await conn.execute(
        "INSERT INTO search_query(tenant_id,site_id,query_hash,ciphertext,nonce,aad_hash,key_version,term_length,token_count,is_question)"
        " VALUES($1,$2,$3,$4,$5,$6,'v1',$7,4,true)",
        t, s, qhash, ciphertext, nonce, aad_hash, len(term),
    )
    await conn.execute(
        "INSERT INTO keyword_cluster_member(tenant_id,cluster_id,site_id,query_hash,impressions) VALUES($1,$2,$3,$4,40)",
        t, ids["cluster"], s, qhash,
    )
    await conn.execute(
        "INSERT INTO content_brief(id,tenant_id,site_id,keyword_cluster_id,analysis_run_id,kind,cluster_label,intent,"
        "priority_score,sections_json,query_hashes,content_hash)"
        " VALUES($1,$2,$3,$4,$5,'new_page','emi formula','informational',30,$6::jsonb,$7,$8)",
        ids["brief"], t, s, ids["cluster"], ids["run"],
        json.dumps([{"title": "Target", "finding": "No page ranks.", "recommendation": "Write one."}]),
        [qhash], "b" * 64,
    )
    await conn.execute(
        "INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status) VALUES($1,$2,$3,$4,'{}'::jsonb,'completed')",
        ids["crawl"], t, s, ids["actor"],
    )
    url = "https://calc.example/emi-calculator"
    await conn.execute(
        "INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash) VALUES($1,$2,$3,$4,$5)",
        ids["page"], t, s, url, hashlib.sha256(url.encode()).hexdigest(),
    )
    await conn.execute(
        "INSERT INTO page_observation(id,tenant_id,page_id,crawl_job_id,http_status,final_url,title,h1_json,word_count,content_hash,rendered)"
        " VALUES($1,$2,$3,$4,200,$5,'EMI Calculator','[]'::jsonb,300,$6,false)",
        uuid4(), t, ids["page"], ids["crawl"], url, "c" * 64,
    )
    if key is not None:
        credential = f"{provider}_api_key"
        aad = credential_aad(t, credential, "v1")
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(KEY).encrypt(nonce, json.dumps({"api_key": key}).encode(), aad)
        await conn.execute(
            "INSERT INTO tenant_credential(tenant_id,provider,config_json,ciphertext,nonce,aad_hash,key_version)"
            " VALUES($1,$2,'{}'::jsonb,$3,$4,$5,'v1')",
            t, credential, sealed, nonce, hashlib.sha256(aad).hexdigest(),
        )
    await conn.execute(
        "INSERT INTO content_draft(id,tenant_id,site_id,content_brief_id,requested_by,idempotency_key,request_hash,provider,author_name)"
        " VALUES($1,$2,$3,$4,$5,$6,$7,$8,'Asha Rao')",
        ids["draft"], t, s, ids["brief"], ids["actor"], f"key-{ids['draft'].hex}", "d" * 64, provider,
    )
    return ids


async def _run(conn, ids, model, *, budget=25_000_000):
    await process_draft(
        conn, ids["tenant"], ids["draft"],
        models={"anthropic": (model, "claude-opus-5"), "openai": (model, "gpt-5")},
        encryption_key=KEY, monthly_budget_micros=budget,
    )
    return await conn.fetchrow("SELECT * FROM content_draft WHERE id=$1", ids["draft"])


async def test_a_queued_draft_is_written_with_the_workspace_key_and_flagged(connection) -> None:
    ids = await _seed(connection)
    model = FakeModel()

    row = await _run(connection, ids, model)

    assert row["status"] == "ready"
    assert model.calls[0]["api_key"] == "sk-ant-test-key-000000"
    assert model.calls[0]["model"] == "claude-opus-5"
    # The decrypted search term and the crawled page both reach the prompt.
    assert "how is emi calculated" in model.calls[0]["user"]
    assert "/emi-calculator" in model.calls[0]["user"]
    assert row["title"] == "How EMI is calculated"
    # 2,000 in and 3,000 out at Opus 5 list price: $0.01 + $0.075.
    assert row["cost_micros"] == 85_000
    flags = json.loads(row["flags_json"])
    assert {flag["kind"] for flag in flags} >= {"claim", "figure"}
    assert json.loads(row["generated_json"])["title"] == "How EMI is calculated"


async def test_without_the_workspace_key_it_fails_rather_than_borrowing_one(connection) -> None:
    ids = await _seed(connection, provider="openai", key=None)
    model = FakeModel()

    row = await _run(connection, ids, model)

    assert row["status"] == "failed"
    assert row["error_code"] == "openai_key_not_configured"
    assert model.calls == []


async def test_the_openai_key_is_used_for_an_openai_draft(connection) -> None:
    ids = await _seed(connection, provider="openai", key="sk-proj-test-key-0000000")
    model = FakeModel()

    row = await _run(connection, ids, model)

    assert row["status"] == "ready"
    assert model.calls[0]["api_key"] == "sk-proj-test-key-0000000"
    assert model.calls[0]["model"] == "gpt-5"


async def test_a_spent_budget_stops_the_call(connection) -> None:
    ids = await _seed(connection)
    await connection.execute("UPDATE content_draft SET cost_micros=100 WHERE id=$1", ids["draft"])
    model = FakeModel()

    row = await _run(connection, ids, model, budget=100)

    assert row["status"] == "failed"
    assert row["error_code"] == "content_draft_budget_exhausted"
    assert model.calls == []


async def test_a_final_provider_error_fails_and_a_transient_one_keeps_the_lease(connection) -> None:
    ids = await _seed(connection)
    row = await _run(connection, ids, FakeModel(DraftModelError("model_refused")))
    assert (row["status"], row["error_code"]) == ("failed", "model_refused")

    other = await _seed(connection)
    with pytest.raises(DraftModelError):
        await _run(connection, other, FakeModel(DraftModelError("provider_rate_limited", retryable=True)))
    row = await connection.fetchrow("SELECT status,attempts,lease_until FROM content_draft WHERE id=$1", other["draft"])
    assert row["status"] == "running"
    assert row["attempts"] == 1
    assert row["lease_until"] is not None
