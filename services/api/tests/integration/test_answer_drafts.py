"""Answer-block drafts: direct answers for a page's question headings, from
request to a high-risk proposal that edits the page's own file, on the
application role under RLS."""

import base64
import json
import secrets
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.services.content_drafts import ContentDraftService, unanswered_questions
from app.services.tenant_credentials import ANTHROPIC_API_KEY, TenantCredentialService, store_for
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SETTINGS = Settings(
    connector_secret_backend="database_envelope",
    connector_secret_encryption_key=SecretStr(
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    ),
)

OUTLINE = {
    "schema_version": 1,
    "headings_truncated": False,
    "faq_schema": {"questions": 0, "visible": 0},
    "headings": [
        {"level": 2, "text": "How is EMI calculated?", "question": True, "answer_words": 0, "answer_kind": "none"},
        {"level": 2, "text": "Why does tenure matter?", "question": True, "answer_words": 40, "answer_kind": "paragraph"},
        {"level": 2, "text": "Pricing", "question": False, "answer_words": 0, "answer_kind": "none"},
        {"level": 3, "text": "Is it free?", "question": True, "answer_words": 4, "answer_kind": "paragraph"},
        {"level": 3, "text": "Which banks?", "question": True, "answer_words": 3, "answer_kind": "list"},
    ],
}

SOURCE = """<html>
  <head><title>EMI</title></head>
  <body>
    <h2>How is EMI calculated?</h2>
    <div class="widget"></div>
    <h3>Is it free?</h3>
    <p>Yes.</p>
  </body>
</html>
"""


@pytest_asyncio.fixture
async def workspace(engine):
    ids = {name: uuid4() for name in ("tenant", "site", "page", "bare_page", "crawl", "actor")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        t, s = ids["tenant"], ids["site"]
        await session.execute(text("INSERT INTO tenant(id,slug,name,status) VALUES(:t,:slug,'w','active')"), {"t": t, "slug": f"q-{t.hex[:8]}"})
        await session.execute(
            text("INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
                 " VALUES(:s,:t,'w','https://w.example','w.example','recommend','active',now())"),
            {"s": s, "t": t},
        )
        for key, url in (("page", "https://w.example/emi"), ("bare_page", "https://w.example/about")):
            await session.execute(
                text("INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash) VALUES(:p,:t,:s,:u,:h)"),
                {"p": ids[key], "t": t, "s": s, "u": url, "h": sha256(url.encode()).hexdigest()},
            )
        await session.execute(
            text("INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status)"
                 " VALUES(:c,:t,:s,:a,'{}'::jsonb,'completed')"),
            {"c": ids["crawl"], "t": t, "s": s, "a": ids["actor"]},
        )
        for key, outline in (("page", OUTLINE), ("bare_page", None)):
            await session.execute(
                text("INSERT INTO page_observation(tenant_id,page_id,crawl_job_id,http_status,final_url,word_count,content_hash,answer_outline_json)"
                     " VALUES(:t,:p,:c,200,'https://w.example/',400,:h,CAST(:o AS jsonb))"),
                {"t": t, "p": ids[key], "c": ids["crawl"], "h": "c" * 64, "o": json.dumps(outline) if outline else None},
            )
    yield ids
    async with factory() as session, session.begin():
        for table in ("outbox_event", "audit_event", "proposal", "content_draft", "tenant_credential",
                      "page_observation", "crawl_job", "page", "site"):
            await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": ids["tenant"]})
        await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": ids["tenant"]})


def ctx(ids: dict[str, UUID], role: Role = Role.OWNER, tenant: UUID | None = None) -> TenantContext:
    return TenantContext(tenant_id=tenant or ids["tenant"], actor_id=ids["actor"], role=role, trace_id="t")


async def with_key(session, ids) -> ContentDraftService:
    await TenantCredentialService(session, ctx(ids)).upsert_ai_key(
        store_for(session, SETTINGS), ANTHROPIC_API_KEY, "sk-ant-abcdefghijklmnop1234"
    )
    return ContentDraftService(session, ctx(ids), SETTINGS)


def test_only_questions_without_a_quotable_answer_are_drafted():
    assert unanswered_questions(OUTLINE) == ["How is EMI calculated?", "Is it free?"]
    assert unanswered_questions(None) == []


async def test_the_server_chooses_the_questions_and_queues_one_live_draft(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service = await with_key(session, workspace)
        draft = await service.request_answers(workspace["page"], "ans-00000001")
        assert (draft.kind, draft.status, draft.content_brief_id) == ("answer_block", "queued", None)
        assert draft.answer_items_json == [
            {"heading": "How is EMI calculated?", "answer": None},
            {"heading": "Is it free?", "answer": None},
        ]
        await session.flush()  # a raw count does not autoflush; the request's commit would
        events = await session.scalar(
            text("SELECT count(*) FROM outbox_event WHERE aggregate_id=:d AND event_type='content_draft.requested.v1'"),
            {"d": draft.id},
        )
        assert events == 1
        assert (await service.request_answers(workspace["page"], "ans-00000001")).id == draft.id
        with pytest.raises(HTTPException) as duplicate:
            await service.request_answers(workspace["page"], "ans-00000002")
        assert duplicate.value.detail == "answer_draft_already_live"


async def test_a_page_with_nothing_to_answer_is_refused(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service = await with_key(session, workspace)
        with pytest.raises(HTTPException) as refused:
            await service.request_answers(workspace["bare_page"], "ans-00000003")
        assert refused.value.detail == "page_has_no_unanswered_questions"


async def test_viewers_and_other_tenants_cannot_request_answers(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(HTTPException) as viewer:
            await ContentDraftService(session, ctx(workspace, Role.VIEWER), SETTINGS).request_answers(
                workspace["page"], "ans-00000004"
            )
        assert viewer.value.status_code == 403
    stranger = uuid4()
    async with tenant_session_factory(stranger) as session:
        with pytest.raises(HTTPException) as hidden:
            await ContentDraftService(session, ctx(workspace, tenant=stranger), SETTINGS).request_answers(
                workspace["page"], "ans-00000005"
            )
        assert hidden.value.status_code == 404


async def ready_answers(session, ids, *, resolved: bool):
    service = await with_key(session, ids)
    draft = await service.request_answers(ids["page"], "ans-00000006")
    # What the worker would have written.
    draft.status = "ready"
    draft.model = "claude-test"
    draft.answer_items_json = [
        {"heading": "How is EMI calculated?", "answer": "EMI spreads principal and interest into equal monthly payments using the loan amount, rate and tenure."},
        {"heading": "Is it free?", "answer": "Yes, it is free."},
    ]
    draft.flags_json = [{"id": "claim-1", "kind": "claim", "text": "x", "resolved": resolved}]
    await session.flush()
    return service, draft


async def test_answer_edits_are_limited_to_the_drafted_questions(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service, draft = await ready_answers(session, workspace, resolved=False)
        edited = await service.update(
            draft.id, expected_version=draft.version, title=None, slug=None, meta_description=None,
            body_markdown=None, author_name=None, resolved_flags={"claim-1": "checked"},
            answers={"Is it free?": "  Yes, the calculator is free  and needs no account. "},
        )
        assert edited.answer_items_json[1]["answer"] == "Yes, the calculator is free and needs no account."
        with pytest.raises(HTTPException) as unknown:
            await service.update(
                draft.id, expected_version=edited.version, title=None, slug=None, meta_description=None,
                body_markdown=None, author_name=None, resolved_flags={}, answers={"What is APR?": "x"},
            )
        assert unknown.value.detail == "answer_heading_unknown"


async def test_reviewed_answers_become_a_high_risk_edit_of_the_page_file(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service, draft = await ready_answers(session, workspace, resolved=True)
        read: list[str] = []

        async def read_file(path: str) -> str | None:
            read.append(path)
            return SOURCE

        proposal = await service.submit(draft.id, read_file, "site/{path}.html")
        assert read == ["site/emi.html"]
        assert (proposal.content_draft_id, proposal.opportunity_id, proposal.target_path) == (draft.id, None, "site/emi.html")
        assert proposal.before_content == SOURCE
        assert "<h2>How is EMI calculated?</h2>\n    <p>EMI spreads" in proposal.after_content
        assert '"@type":"FAQPage"' in proposal.after_content
        # Claim-changing copy: high risk however short, awaiting two approvers.
        assert proposal.risk == "high"
        assert proposal.status == "review_required"
        assert proposal.policy_evaluation_json["can_auto_deploy"] is False
        refreshed = await service.get(draft.id)
        assert refreshed is not None and refreshed.status == "submitted"


async def test_answers_are_not_submitted_unreviewed_or_without_a_file(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service, draft = await ready_answers(session, workspace, resolved=False)

        async def read_file(path: str) -> str | None:
            return SOURCE

        with pytest.raises(HTTPException) as unresolved:
            await service.submit(draft.id, read_file, "{path}.html")
        assert unresolved.value.detail == "content_draft_flags_unresolved"

        draft.flags_json = [{"id": "claim-1", "kind": "claim", "text": "x", "resolved": True}]
        await session.flush()
        with pytest.raises(HTTPException) as unconnected:
            await service.submit(draft.id)
        assert unconnected.value.detail == "github_not_connected"

        async def missing(path: str) -> str | None:
            return None

        with pytest.raises(HTTPException) as absent:
            await service.submit(draft.id, missing, "{path}.html")
        assert absent.value.detail == "draft_target_not_found:emi.html"
