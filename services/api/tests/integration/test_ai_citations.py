"""Tracking questions for observed AI citations, and reading the results,
on the application role under RLS."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role, TenantContext
from app.services.ai_citations import MAX_TRACKED, AiCitationService, as_question
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


@pytest_asyncio.fixture
async def workspace(engine):
    ids = {name: uuid4() for name in ("tenant", "site", "other_site", "run", "cluster", "foreign_cluster", "actor")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        t = ids["tenant"]
        await session.execute(text("INSERT INTO tenant(id,slug,name,status) VALUES(:t,:slug,'w','active')"), {"t": t, "slug": f"a-{t.hex[:8]}"})
        for key, host in (("site", "calc.example"), ("other_site", "other.example")):
            await session.execute(
                text("INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
                     " VALUES(:s,:t,'Calc',:o,:h,'observe','active',now())"),
                {"s": ids[key], "t": t, "o": f"https://{host}", "h": host},
            )
        await session.execute(
            text("INSERT INTO keyword_analysis_run(id,tenant_id,site_id,algorithm_version,window_start,window_end,content_hash)"
                 " VALUES(:r,:t,:s,'t','2026-09-01','2026-09-20',:h)"),
            {"r": ids["run"], "t": t, "s": ids["site"], "h": "a" * 64},
        )
        for cluster, label, question in (("cluster", "how is emi calculated", True), ("foreign_cluster", "bmi chart", False)):
            await session.execute(
                text("INSERT INTO keyword_cluster(id,tenant_id,site_id,analysis_run_id,label,cluster_key,intent,member_count,"
                     "opportunity_score,answer_engine_candidate) VALUES(:c,:t,:s,:r,:l,:l,'informational',1,30,:q)"),
                {"c": ids[cluster], "t": t, "s": ids["site"], "r": ids["run"], "l": label, "q": question},
            )
    yield ids
    async with factory() as session, session.begin():
        for table in ("outbox_event", "routine", "ai_citation_observation", "ai_citation_run", "ai_citation_prompt", "audit_event",
                      "keyword_cluster", "keyword_analysis_run", "site"):
            await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": ids["tenant"]})
        await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": ids["tenant"]})


def ctx(ids: dict[str, UUID], role: Role = Role.OWNER, tenant: UUID | None = None) -> TenantContext:
    return TenantContext(tenant_id=tenant or ids["tenant"], actor_id=ids["actor"], role=role, trace_id="t")


def test_a_cluster_label_reads_as_its_question():
    assert as_question("how is emi calculated") == "How is emi calculated?"
    assert as_question("emi formula") == "Emi formula"


async def test_question_clusters_are_suggested_until_tracked(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service = AiCitationService(session, ctx(workspace))
        tracked, suggestions = await service.prompts(workspace["site"])
        assert tracked == []
        assert suggestions == [{"prompt": "How is emi calculated?", "keyword_cluster_id": workspace["cluster"]}]

        prompt = await service.track(workspace["site"], "How is emi calculated?", workspace["cluster"])
        assert prompt.source == "question_cluster"
        tracked, suggestions = await service.prompts(workspace["site"])
        assert [row.prompt for row in tracked] == ["How is emi calculated?"]
        assert suggestions == []
        audit = await session.execute(text("SELECT action,metadata FROM audit_event WHERE resource_id=:p"), {"p": str(prompt.id)})
        action, metadata = audit.one()
        assert action == "ai_citation_prompt.tracked"
        # The audit trail records that a question changed, not its text.
        assert "How is" not in json.dumps(metadata)


async def test_duplicates_and_the_cap_are_refused(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service = AiCitationService(session, ctx(workspace))
        await service.track(workspace["site"], "What is an EMI calculator", None)
        with pytest.raises(HTTPException) as duplicate:
            await service.track(workspace["site"], "  what is an   EMI calculator ", None)
        assert duplicate.value.detail == "prompt_already_tracked"
        for index in range(MAX_TRACKED - 1):
            await service.track(workspace["site"], f"Question number {index} about loans", None)
        with pytest.raises(HTTPException) as capped:
            await service.track(workspace["site"], "One question too many here", None)
        assert capped.value.detail == "prompt_limit_reached"


async def test_untracking_retires_and_retracking_revives(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service = AiCitationService(session, ctx(workspace))
        prompt = await service.track(workspace["site"], "Best free EMI calculator", None)
        await service.untrack(workspace["site"], prompt.id)
        tracked, _ = await service.prompts(workspace["site"])
        assert tracked == []
        again = await service.track(workspace["site"], "Best free EMI calculator", None)
        assert again.id == prompt.id and again.active


async def test_a_cluster_from_another_site_is_not_linked(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        prompt = await AiCitationService(session, ctx(workspace)).track(
            workspace["other_site"], "How is emi calculated here?", workspace["cluster"]
        )
        assert prompt.keyword_cluster_id is None and prompt.source == "manual"


async def test_viewers_read_but_cannot_change_what_is_tracked(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        viewer = AiCitationService(session, ctx(workspace, Role.VIEWER))
        await viewer.prompts(workspace["site"])
        with pytest.raises(HTTPException) as refused:
            await viewer.track(workspace["site"], "How is emi calculated?", None)
        assert refused.value.status_code == 403


async def test_another_tenant_cannot_reach_the_site(tenant_session_factory, workspace):
    stranger = uuid4()
    async with tenant_session_factory(stranger) as session:
        service = AiCitationService(session, ctx(workspace, tenant=stranger))
        for call in (service.prompts(workspace["site"]), service.report(workspace["site"])):
            with pytest.raises(HTTPException) as refused:
                await call
            assert refused.value.status_code == 404


async def test_the_report_returns_the_latest_run_and_its_answers(tenant_session_factory, workspace):
    async with tenant_session_factory(workspace["tenant"]) as session:
        service = AiCitationService(session, ctx(workspace))
        prompt = await service.track(workspace["site"], "How is emi calculated?", None)
        for day, cited in ((1, 0), (8, 1)):
            run_id = uuid4()
            await session.execute(
                text("INSERT INTO ai_citation_run(id,tenant_id,site_id,started_at,finished_at,prompts_asked,answers,cited,mentioned)"
                     " VALUES(:r,:t,:s,:at,:at,2,2,:c,1)"),
                {"r": run_id, "t": workspace["tenant"], "s": workspace["site"], "at": datetime(2026, 9, day, 6, tzinfo=UTC), "c": cited},
            )
            for provider in ("anthropic", "openai"):
                await session.execute(
                    text("INSERT INTO ai_citation_observation(tenant_id,site_id,run_id,prompt_id,prompt,provider,model,status,"
                         "site_cited,own_citation_rank,cited_hosts,answer_excerpt) VALUES(:t,:s,:r,:p,:q,:v,'m','answered',:c,:rank,"
                         "'[\"calc.example\"]'::jsonb,'An excerpt.')"),
                    {"t": workspace["tenant"], "s": workspace["site"], "r": run_id, "p": prompt.id, "q": prompt.prompt,
                     "v": provider, "c": bool(cited) and provider == "anthropic", "rank": 1 if cited and provider == "anthropic" else None},
                )
        latest, observations, history = await service.report(workspace["site"])
        assert latest is not None and latest.cited == 1
        assert [(row.provider, row.site_cited, row.own_citation_rank) for row in observations] == [
            ("anthropic", True, 1), ("openai", False, None),
        ]
        assert [run.cited for run in history] == [1, 0]


async def test_the_citation_scan_can_run_weekly_but_never_daily(tenant_session_factory, workspace):
    from app.api.schemas import RoutineUpsert
    from app.services.routines import RoutineService

    async with tenant_session_factory(workspace["tenant"]) as session:
        service = RoutineService(session, ctx(workspace))
        with pytest.raises(HTTPException) as refused:
            await service.upsert(workspace["site"], RoutineUpsert(kind="ai_citation_scan", cadence="daily"))
        assert refused.value.detail == "paid_routine_cadence_too_frequent"
        routine = await service.upsert(
            workspace["site"], RoutineUpsert(kind="ai_citation_scan", cadence="weekly", schedule_isodow=1)
        )
        assert routine.cadence == "weekly"
