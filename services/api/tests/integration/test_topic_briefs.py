"""New-post briefs from a topic a person names, on the application role under RLS.

A site too young to rank for anything it has no page for never gets a
new-post brief from keyword analysis; these pin the way in that does not need
search data, and the refusals that keep it from competing with the site's own
pages.
"""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.schemas import (
    ContentBriefRead,
    ContentBriefStatus,
    ContentBriefStatusUpdate,
    TopicBriefCreate,
)
from app.core.context import Role, TenantContext
from app.services.briefs import ContentBriefService
from app.services.content_drafts import ContentDraftService
from app.services.tenant_credentials import OPENAI_API_KEY, TenantCredentialService, store_for
from tests.conftest import requires_database
from tests.integration.test_content_drafts import SETTINGS

pytestmark = [pytest.mark.asyncio, requires_database]


@pytest_asyncio.fixture
async def workspace(engine):
    ids = {name: uuid4() for name in ("tenant", "other_tenant", "site", "page", "crawl", "actor")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        for tenant in ("tenant", "other_tenant"):
            await session.execute(
                text("INSERT INTO tenant(id,slug,name,status) VALUES(:t,:slug,'w','active')"),
                {"t": ids[tenant], "slug": f"tb-{ids[tenant].hex[:8]}"},
            )
        t, s = ids["tenant"], ids["site"]
        await session.execute(
            text("INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
                 " VALUES(:s,:t,'TheCalcHive','https://calc.example','calc.example','observe','active',now())"),
            {"s": s, "t": t},
        )
        url = "https://calc.example/emi-calculator"
        await session.execute(
            text("INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash) VALUES(:p,:t,:s,:u,:h)"),
            {"p": ids["page"], "t": t, "s": s, "u": url, "h": sha256(url.encode()).hexdigest()},
        )
        await session.execute(
            text("INSERT INTO crawl_job(id,tenant_id,site_id,status,requested_by,config_snapshot)"
                 " VALUES(:c,:t,:s,'completed',:a,'{}'::jsonb)"),
            {"c": ids["crawl"], "t": t, "s": s, "a": ids["actor"]},
        )
        await session.execute(
            text("INSERT INTO page_observation(tenant_id,page_id,crawl_job_id,http_status,final_url,title)"
                 " VALUES(:t,:p,:c,200,:u,'EMI Calculator — Free Online Tool | CalcHive')"),
            {"t": t, "p": ids["page"], "c": ids["crawl"], "u": url},
        )
    yield ids
    async with factory() as session, session.begin():
        for table in ("outbox_event", "audit_event", "content_draft", "tenant_credential", "content_brief",
                      "page_observation", "crawl_job", "page", "site"):
            await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": ids["tenant"]})
        for tenant in ("tenant", "other_tenant"):
            await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": ids[tenant]})


def ctx(ids: dict[str, UUID], role: Role = Role.OWNER, tenant: UUID | None = None) -> TenantContext:
    return TenantContext(tenant_id=tenant or ids["tenant"], actor_id=ids["actor"], role=role, trace_id="t")


def topic(value: str) -> TopicBriefCreate:
    return TopicBriefCreate(topic=value)


async def test_a_topic_becomes_a_new_post_brief_that_can_be_drafted(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        brief = await ContentBriefService(session, ctx(workspace)).create_topic(
            workspace["site"], topic("  How is   EMI calculated in India? ")
        )
        read = ContentBriefRead.model_validate(brief)
        assert (read.kind, read.source, read.status, read.keyword_cluster_id) == (
            "new_page", "topic", "queued", None
        )
        assert read.cluster_label == "How is EMI calculated in India?"
        assert brief.requested_by == workspace["actor"]
        assert [section["key"] for section in brief.sections_json] == ["target", "on_page"]
        audit = await session.scalar(
            text("SELECT count(*) FROM audit_event WHERE resource_id=:b AND action='content_brief.requested'"),
            {"b": str(brief.id)},
        )
        assert audit == 1

        # The usual drafting path takes it from here.
        await TenantCredentialService(session, ctx(workspace)).upsert_ai_key(
            store_for(session, SETTINGS), OPENAI_API_KEY, "sk-proj-abcdefghijklmnop1234"
        )
        draft = await ContentDraftService(session, ctx(workspace), SETTINGS).request(
            brief.id, "topic-key-0001", "Masood", "openai"
        )
        assert (draft.status, draft.content_brief_id) == ("queued", brief.id)
        await session.commit()


async def test_a_topic_a_page_already_targets_is_refused_and_the_page_named(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(HTTPException) as refused:
            await ContentBriefService(session, ctx(workspace)).create_topic(workspace["site"], topic("EMI calculator"))
    assert refused.value.status_code == 409
    assert refused.value.detail == "topic_already_has_page:https://calc.example/emi-calculator"


async def test_one_live_brief_per_topic_until_it_is_dismissed(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service = ContentBriefService(session, ctx(workspace))
        first = await service.create_topic(workspace["site"], topic("Saving for a house deposit"))
        with pytest.raises(HTTPException) as duplicate:
            await service.create_topic(workspace["site"], topic("saving for a HOUSE deposit"))
        assert duplicate.value.detail == "topic_brief_exists"

        await service.update_status(
            first.id, ContentBriefStatusUpdate(status=ContentBriefStatus.DISMISSED, reason="not now")
        )
        again = await service.create_topic(workspace["site"], topic("Saving for a house deposit"))
        assert again.id != first.id
        await session.commit()


async def test_viewers_and_other_workspaces_cannot_plan_posts(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(HTTPException) as viewer:
            await ContentBriefService(session, ctx(workspace, Role.VIEWER)).create_topic(
                workspace["site"], topic("Saving for a house deposit")
            )
    assert viewer.value.detail == "insufficient_permissions_for_brief"

    async with tenant_session_factory(workspace["other_tenant"]) as session:
        with pytest.raises(HTTPException) as stranger:
            await ContentBriefService(session, ctx(workspace, tenant=workspace["other_tenant"])).create_topic(
                workspace["site"], topic("Saving for a house deposit")
            )
    assert stranger.value.detail == "site_not_found"


async def test_the_database_refuses_a_topic_brief_shaped_like_an_analysis_one(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        with pytest.raises(Exception, match="content_brief_origin_check"):
            await session.execute(
                text("INSERT INTO content_brief(tenant_id,site_id,source,kind,target_page_id,cluster_label,intent,"
                     "priority_score,content_hash,requested_by) VALUES(:t,:s,'topic','refresh',:p,'x','informational',0,:h,:a)"),
                {"t": workspace["tenant"], "s": workspace["site"], "p": workspace["page"], "h": "c" * 64, "a": workspace["actor"]},
            )


def test_a_topic_is_plain_words():
    assert topic("  a   b c ").topic == "a b c"
    for bad in ("ab", "<script>x</script>", "x" * 121):
        with pytest.raises(ValidationError):
            topic(bad)
