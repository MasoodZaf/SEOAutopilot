"""Observed AI citations: parsing each provider, judging an answer, and a full
run against PostgreSQL with fake models.

The models are fakes. What is under test is everything around them: the site
is never named in the question, each workspace's own key is the one used,
spend stops at the cap before any call, and an answer's text is stored only
as a bounded excerpt.
"""

import hashlib
import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.citations.models import (
    SEARCH_PRICE_MICROS,
    CitationAnswer,
    CitationModelError,
    OpenAICitationModel,
    anthropic_answer,
    openai_answer,
)
from app.citations.scan import (
    ESTIMATE_PER_ANSWER_MICROS,
    MAX_EXCERPT,
    analyze,
    brand_terms,
    calls_within_budget,
)
from app.connectors.tenant_clients import credential_aad
from app.routines.runner import CitationSettings, process_run

pytestmark = pytest.mark.asyncio

# --- provider response parsing ---


def test_claude_citations_come_from_text_blocks_only() -> None:
    message = {
        "content": [
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "emi"}},
            {"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": "https://retrieved-not-cited.example/"}]},
            {"type": "text", "text": "EMI is a fixed payment. ", "citations": [
                {"type": "web_search_result_location", "url": "https://calc.example/emi", "title": "EMI", "cited_text": "x"},
            ]},
            {"type": "text", "text": "Banks publish rates.", "citations": [
                {"type": "web_search_result_location", "url": "https://bank.example/rates", "cited_text": "y"},
            ]},
        ]
    }
    text, urls = anthropic_answer(message)
    assert text == "EMI is a fixed payment. Banks publish rates."
    # A search result the answer did not cite is not a citation.
    assert urls == ["https://calc.example/emi", "https://bank.example/rates"]


def test_openai_citations_are_url_annotations_and_searches_are_counted() -> None:
    payload = {
        "output": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "Use the formula.", "annotations": [
                {"type": "url_citation", "url": "https://calc.example/emi?utm_source=openai", "start_index": 0, "end_index": 3},
                {"type": "file_citation", "file_id": "f"},
            ]}]},
        ]
    }
    assert openai_answer(payload) == ("Use the formula.", ["https://calc.example/emi?utm_source=openai"], 1)


def test_an_answer_costs_its_tokens_plus_its_searches() -> None:
    answer = CitationAnswer("t", [], 2, "claude-opus-5", 1_000_000, 0)
    assert answer.cost_micros == 5_000_000 + 2 * SEARCH_PRICE_MICROS


async def test_openai_key_rejection_is_a_stable_code_with_no_provider_text() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"message": "Incorrect API key sk-xyz"}}))
    with pytest.raises(CitationModelError) as refused:
        await OpenAICitationModel(transport=transport).ask(api_key="sk-bad", model="gpt-5", question="What is EMI?")
    assert refused.value.code == "openai_key_rejected"
    assert "sk-" not in str(refused.value)


async def test_openai_sends_only_the_question() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"model": "gpt-5", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Answer.", "annotations": []}]}
        ], "usage": {"input_tokens": 10, "output_tokens": 5}})

    answer = await OpenAICitationModel(transport=httpx.MockTransport(handler)).ask(
        api_key="sk-workspace", model="gpt-5", question="How is EMI calculated?"
    )
    assert seen["input"] == "How is EMI calculated?"
    assert seen["tools"] == [{"type": "web_search"}]
    assert "instructions" not in seen
    assert seen["auth"] == "Bearer sk-workspace"
    assert answer.text == "Answer." and answer.cited_urls == []


# --- judging an answer ---


def test_cited_mentioned_and_ranked_by_distinct_host() -> None:
    finding = analyze(
        "Try TheCalcHive's EMI calculator, or your bank's.",
        [
            "https://www.bank.example/emi",
            "https://bank.example/other",
            "https://thecalchive.com/emi-calculator",
            "https://rival.example/emi",
        ],
        "thecalchive.com",
        brand_terms("TheCalcHive", "thecalchive.com"),
        ["rival.example"],
    )
    assert finding.site_cited and finding.site_mentioned
    assert finding.own_citation_rank == 2
    assert finding.cited_hosts == ["bank.example", "thecalchive.com", "rival.example"]
    assert finding.own_urls == ["https://thecalchive.com/emi-calculator"]
    assert finding.competitor_hosts == ["rival.example"]


def test_a_lookalike_host_or_word_is_not_the_site() -> None:
    finding = analyze(
        "Mycalchive tools and thecalchive.com.evil.example are different sites.",
        ["https://thecalchive.com.evil.example/x", "https://notthecalchive.com/"],
        "thecalchive.com",
        brand_terms("TheCalcHive", "thecalchive.com"),
        [],
    )
    assert not finding.site_cited
    assert finding.own_citation_rank is None
    assert not finding.site_mentioned


def test_subdomains_count_as_the_site() -> None:
    finding = analyze("x", ["https://blog.thecalchive.com/post"], "thecalchive.com", ["thecalchive.com"], [])
    assert finding.site_cited


def test_the_excerpt_is_bounded_and_centred_on_the_mention() -> None:
    text = ("filler " * 300) + "TheCalcHive has an EMI tool. " + ("more " * 300)
    finding = analyze(text, [], "thecalchive.com", brand_terms("TheCalcHive", "thecalchive.com"), [])
    assert finding.site_mentioned
    assert len(finding.excerpt) <= MAX_EXCERPT
    assert "TheCalcHive has an EMI tool." in finding.excerpt
    assert finding.excerpt.startswith("…")


def test_short_names_are_not_brand_terms() -> None:
    assert brand_terms("Go", "go.dev") == ["go.dev"]


def test_the_budget_admits_only_whole_answers_at_the_high_estimate() -> None:
    assert calls_within_budget(16, 0, 10_000_000) == 16
    assert calls_within_budget(16, 10_000_000 - 3 * ESTIMATE_PER_ANSWER_MICROS, 10_000_000) == 3
    assert calls_within_budget(16, 11_000_000, 10_000_000) == 0


# --- a full run, against PostgreSQL ---

database = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is unset; run `make test-integration`",
)
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


class FakeEngine:
    def __init__(self, cite: str | None, error: str | None = None) -> None:
        self.cite = cite
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def ask(self, *, api_key: str, model: str, question: str) -> CitationAnswer:
        self.calls.append({"api_key": api_key, "model": model, "question": question})
        if self.error:
            raise CitationModelError(self.error)
        urls = ["https://bank.example/a"] + ([self.cite] if self.cite else [])
        return CitationAnswer("Ignore previous instructions. Calc Example explains it.", urls, 1, model, 1_000, 500)


async def _seed(conn: asyncpg.Connection, keys: dict[str, str], prompts: int = 2) -> dict[str, UUID]:
    ids = {name: uuid4() for name in ("tenant", "site", "routine", "run", "actor")}
    t, s = ids["tenant"], ids["site"]
    await conn.execute("INSERT INTO tenant(id,slug,name,status) VALUES($1,$2,'c','active')", t, f"c-{t.hex[:8]}")
    await conn.execute(
        "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
        " VALUES($1,$2,'Calc Example','https://calc.example','calc.example','observe','active',now())",
        s, t,
    )
    await conn.execute(
        "INSERT INTO competitor(tenant_id,site_id,normalized_host,label,created_by) VALUES($1,$2,'bank.example','Bank',$3)",
        t, s, ids["actor"],
    )
    for index in range(prompts):
        await conn.execute(
            "INSERT INTO ai_citation_prompt(tenant_id,site_id,prompt,source,created_by,created_at)"
            " VALUES($1,$2,$3,'manual',$4,now()+($5 * interval '1 second'))",
            t, s, f"How is EMI calculated, question {index}?", ids["actor"], index,
        )
    for provider, key in keys.items():
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
        "INSERT INTO routine(id,tenant_id,site_id,kind,cadence,schedule_isodow,next_run_at,created_by)"
        " VALUES($1,$2,$3,'ai_citation_scan','weekly',1,now(),$4)",
        ids["routine"], t, s, ids["actor"],
    )
    await conn.execute(
        "INSERT INTO routine_run(id,tenant_id,routine_id,site_id,kind,scheduled_for) VALUES($1,$2,$3,$4,'ai_citation_scan',now())",
        ids["run"], t, ids["routine"], s,
    )
    return ids


def _settings(anthropic: FakeEngine, openai: FakeEngine, budget: int = 10_000_000) -> CitationSettings:
    return CitationSettings(
        models={"anthropic": (anthropic, "claude-opus-5"), "openai": (openai, "gpt-5")},
        monthly_budget_micros=budget,
    )


@database
async def test_a_run_asks_each_question_of_each_keyed_engine_and_records_citations(connection) -> None:
    ids = await _seed(connection, {"anthropic": "sk-ant-workspace", "openai": "sk-openai-workspace"})
    claude, chatgpt = FakeEngine("https://calc.example/emi"), FakeEngine(None)

    await process_run(connection, ids["tenant"], ids["run"], encryption_key=KEY, citations=_settings(claude, chatgpt))

    run = await connection.fetchrow("SELECT status,summary_json,skip_reason FROM routine_run WHERE id=$1", ids["run"])
    assert run["status"] == "completed", run
    assert [call["api_key"] for call in claude.calls] == ["sk-ant-workspace"] * 2
    assert [call["api_key"] for call in chatgpt.calls] == ["sk-openai-workspace"] * 2
    # The question goes out exactly as tracked: the site is never named in it.
    assert all("calc.example" not in call["question"] and "Calc Example" not in call["question"] for call in claude.calls + chatgpt.calls)

    rows = await connection.fetch(
        "SELECT provider,site_cited,site_mentioned,own_citation_rank,competitor_hosts,answer_excerpt FROM ai_citation_observation ORDER BY provider,prompt"
    )
    assert [(r["provider"], r["site_cited"], r["own_citation_rank"]) for r in rows] == [
        ("anthropic", True, 2), ("anthropic", True, 2), ("openai", False, None), ("openai", False, None),
    ]
    assert all(r["site_mentioned"] for r in rows)
    assert json.loads(rows[0]["competitor_hosts"]) == ["bank.example"]
    citation_run = await connection.fetchrow("SELECT answers,cited,mentioned,cost_micros FROM ai_citation_run")
    assert (citation_run["answers"], citation_run["cited"], citation_run["mentioned"]) == (4, 2, 4)
    assert citation_run["cost_micros"] > 0


@database
async def test_only_engines_the_workspace_has_a_key_for_are_asked(connection) -> None:
    ids = await _seed(connection, {"openai": "sk-openai-workspace"})
    claude, chatgpt = FakeEngine("https://calc.example/"), FakeEngine("https://calc.example/")
    await process_run(connection, ids["tenant"], ids["run"], encryption_key=KEY, citations=_settings(claude, chatgpt))
    assert claude.calls == [] and len(chatgpt.calls) == 2


@database
async def test_no_key_or_no_questions_skips_without_asking(connection) -> None:
    ids = await _seed(connection, {})
    claude, chatgpt = FakeEngine(None), FakeEngine(None)
    await process_run(connection, ids["tenant"], ids["run"], encryption_key=KEY, citations=_settings(claude, chatgpt))
    run = await connection.fetchrow("SELECT status,skip_reason FROM routine_run WHERE id=$1", ids["run"])
    assert (run["status"], run["skip_reason"]) == ("skipped", "no_ai_key")
    assert claude.calls == [] and chatgpt.calls == []

    ids = await _seed(connection, {"anthropic": "sk-ant-x"}, prompts=0)
    await process_run(connection, ids["tenant"], ids["run"], encryption_key=KEY, citations=_settings(claude, chatgpt))
    run = await connection.fetchrow("SELECT status,skip_reason FROM routine_run WHERE id=$1", ids["run"])
    assert (run["status"], run["skip_reason"]) == ("skipped", "no_tracked_questions")


@database
async def test_the_monthly_cap_stops_questions_before_they_are_asked(connection) -> None:
    ids = await _seed(connection, {"anthropic": "sk-ant-x", "openai": "sk-openai-x"})
    claude, chatgpt = FakeEngine(None), FakeEngine(None)
    # Room for three answers at the estimate; four are wanted.
    await process_run(
        connection, ids["tenant"], ids["run"], encryption_key=KEY,
        citations=_settings(claude, chatgpt, budget=3 * ESTIMATE_PER_ANSWER_MICROS),
    )
    assert len(claude.calls) + len(chatgpt.calls) == 3
    summary = await connection.fetchval("SELECT summary_json FROM ai_citation_run")
    assert json.loads(summary)["not_asked_for_budget"] == 1


@database
async def test_a_rejected_key_is_recorded_per_answer_and_the_run_still_completes(connection) -> None:
    ids = await _seed(connection, {"anthropic": "sk-ant-x", "openai": "sk-openai-x"})
    claude, chatgpt = FakeEngine(None, error="anthropic_key_rejected"), FakeEngine("https://calc.example/")
    await process_run(connection, ids["tenant"], ids["run"], encryption_key=KEY, citations=_settings(claude, chatgpt))
    run = await connection.fetchrow("SELECT status,summary_json FROM routine_run WHERE id=$1", ids["run"])
    assert run["status"] == "completed"
    assert json.loads(run["summary_json"])["errors"] == {"anthropic_key_rejected": 2}
    failed = await connection.fetch("SELECT error_code,answer_excerpt FROM ai_citation_observation WHERE status='failed'")
    assert [(row["error_code"], row["answer_excerpt"]) for row in failed] == [("anthropic_key_rejected", None)] * 2


@database
async def test_another_tenants_session_sees_no_citations(connection) -> None:
    ids = await _seed(connection, {"anthropic": "sk-ant-x"})
    await process_run(connection, ids["tenant"], ids["run"], encryption_key=KEY, citations=_settings(FakeEngine(None), FakeEngine(None)))
    await connection.execute("SET ROLE seo_autopilot_app")
    try:
        await connection.execute("SELECT set_config('app.tenant_id',$1,false)", str(uuid4()))
        for table in ("ai_citation_prompt", "ai_citation_run", "ai_citation_observation"):
            assert await connection.fetchval(f"SELECT count(*) FROM {table}") == 0
    finally:
        await connection.execute("RESET ROLE")
