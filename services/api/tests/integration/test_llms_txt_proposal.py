"""Proposing an llms.txt: only when the crawl saw none, once, and as a
high-risk new file that goes through the ordinary approval gates."""

import json
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.services.llms_txt import LlmsTxtService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SETTINGS = Settings()


def _summary(status: str | None) -> str:
    if status is None:
        return json.dumps({"pages_observed": 3})
    return json.dumps({"ai_access": {"llms_txt": {"status": status, "bytes": 0, "links": 0, "has_title": False}}})


@pytest_asyncio.fixture
async def workspace(engine):
    ids = {name: uuid4() for name in ("tenant", "site", "crawl", "actor")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        t, s = ids["tenant"], ids["site"]
        await session.execute(text("INSERT INTO tenant(id,slug,name,status) VALUES(:t,:slug,'w','active')"), {"t": t, "slug": f"l-{t.hex[:8]}"})
        await session.execute(
            text("INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
                 " VALUES(:s,:t,'Acme Tools','https://acme.example','acme.example','recommend','active',now())"),
            {"s": s, "t": t},
        )
        await session.execute(
            text("INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status,finished_at,result_summary)"
                 " VALUES(:c,:t,:s,:a,'{}'::jsonb,'completed',now(),CAST(:summary AS jsonb))"),
            {"c": ids["crawl"], "t": t, "s": s, "a": ids["actor"], "summary": _summary("missing")},
        )
        pages = [
            ("https://acme.example/", "Acme Tools", "Tools for makers.", 200, [], None),
            ("https://acme.example/tools/saw", "Saw guide", "Pick a saw.", 200, [], None),
            ("https://acme.example/drafts/wip", "Work in progress", None, 200, ["noindex"], None),
            ("https://acme.example/old", "Old page", None, 200, [], "https://acme.example/"),
            ("https://acme.example/gone", "Gone", None, 404, [], None),
        ]
        for url, title, description, code, robots, canonical in pages:
            page_id = uuid4()
            await session.execute(
                text("INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash) VALUES(:p,:t,:s,:u,:h)"),
                {"p": page_id, "t": t, "s": s, "u": url, "h": sha256(url.encode()).hexdigest()},
            )
            await session.execute(
                text("INSERT INTO page_observation(tenant_id,page_id,crawl_job_id,http_status,final_url,title,"
                     "meta_description,word_count,content_hash,robots_directives,canonical_url)"
                     " VALUES(:t,:p,:c,:code,:u,:title,:d,300,:h,:robots,:canonical)"),
                {"t": t, "p": page_id, "c": ids["crawl"], "code": code, "u": url, "title": title,
                 "d": description, "h": "c" * 64, "robots": robots, "canonical": canonical},
            )
    yield ids
    async with factory() as session, session.begin():
        for table in ("outbox_event", "audit_event", "proposal", "connector", "page_observation", "page", "crawl_job", "site"):
            await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": ids["tenant"]})
        await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": ids["tenant"]})


def ctx(ids: dict[str, UUID], role: Role = Role.OWNER) -> TenantContext:
    return TenantContext(tenant_id=ids["tenant"], actor_id=ids["actor"], role=role, trace_id="t")


async def _connect(session, ids, config: str = '{"repository":"acme/site","base_branch":"main"}') -> None:
    await session.execute(
        text("INSERT INTO connector(tenant_id,site_id,type,status,provider_key,config_json)"
             " VALUES(:t,:s,'github_repository','active','github_app',CAST(:c AS jsonb))"),
        {"t": ids["tenant"], "s": ids["site"], "c": config},
    )


async def _set_llms_status(session, ids, status: str | None) -> None:
    await session.execute(
        text("UPDATE crawl_job SET result_summary=CAST(:summary AS jsonb) WHERE id=:c"),
        {"c": ids["crawl"], "summary": _summary(status)},
    )


async def test_a_missing_llms_txt_becomes_a_high_risk_new_file_listing_indexable_pages(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        await _connect(session, workspace)
        proposal = await LlmsTxtService(session, ctx(workspace), SETTINGS).propose(workspace["site"])

        assert proposal.target_path == "public/llms.txt"
        assert proposal.before_content == ""
        assert proposal.risk == "high"
        assert proposal.status == "review_required"
        assert proposal.policy_evaluation_json["required_approver_count"] >= 2
        assert proposal.content_draft_id is None and proposal.opportunity_id is None
        body = proposal.after_content
        assert body.startswith("# Acme Tools\n\n> Tools for makers.\n")
        assert "- [Saw guide](https://acme.example/tools/saw): Pick a saw." in body
        # Noindex, canonicalised-away and failing pages are not advertised.
        for excluded in ("Work in progress", "Old page", "Gone"):
            assert excluded not in body
        page = await session.execute(
            text("SELECT normalized_url,lifecycle_status FROM page WHERE id=:p"), {"p": proposal.page_id}
        )
        assert tuple(page.one()) == ("https://acme.example/llms.txt", "planned")

        # One at a time: a second request while the first is open is refused.
        with pytest.raises(HTTPException) as again:
            await LlmsTxtService(session, ctx(workspace), SETTINGS).propose(workspace["site"])
        assert again.value.detail == "llms_txt_proposal_exists"


async def test_the_connector_can_name_where_the_site_serves_it(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        await _connect(session, workspace, '{"repository":"acme/site","llms_txt_path":"static/llms.txt"}')
        proposal = await LlmsTxtService(session, ctx(workspace), SETTINGS).propose(workspace["site"])
        assert proposal.target_path == "static/llms.txt"


@pytest.mark.parametrize(
    ("status", "detail"),
    [("present", "llms_txt_present"), ("invalid", "llms_txt_invalid"), (None, "recrawl_required")],
)
async def test_nothing_is_proposed_unless_the_crawl_saw_no_file(tenant_session_factory, workspace, status, detail):
    async with tenant_session_factory(workspace["tenant"]) as session:
        await _connect(session, workspace)
        await _set_llms_status(session, workspace, status)
        with pytest.raises(HTTPException) as refused:
            await LlmsTxtService(session, ctx(workspace), SETTINGS).propose(workspace["site"])
        assert refused.value.detail == detail


async def test_refused_without_github_and_for_viewers(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(HTTPException) as unconnected:
            await LlmsTxtService(session, ctx(workspace), SETTINGS).propose(workspace["site"])
        assert unconnected.value.detail == "github_not_connected"
        await _connect(session, workspace)
        with pytest.raises(HTTPException) as viewer:
            await LlmsTxtService(session, ctx(workspace, Role.VIEWER), SETTINGS).propose(workspace["site"])
        assert viewer.value.status_code == 403


async def test_another_tenant_cannot_reach_the_site(tenant_session_factory, workspace):
    stranger = {**workspace, "tenant": uuid4()}
    async with tenant_session_factory(stranger["tenant"]) as session:
        with pytest.raises(HTTPException) as refused:
            await LlmsTxtService(session, ctx(stranger), SETTINGS).propose(workspace["site"])
        assert refused.value.status_code == 404
