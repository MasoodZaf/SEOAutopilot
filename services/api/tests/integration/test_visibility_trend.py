"""The zero-click scorecard against PostgreSQL, on the application role under RLS."""

import hashlib
import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role, TenantContext
from app.services.keywords import query_aad
from app.services.visibility import VisibilityService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

KEY = bytes(range(32))
TODAY = date(2026, 10, 3)
# The last full week the trend reads: Search Console settles two days behind.
THIS_WEEK = date(2026, 9, 28)
LAST_WEEK = THIS_WEEK - timedelta(weeks=1)


@pytest_asyncio.fixture
async def site(engine):
    ids = {name: uuid4() for name in ("tenant", "site", "connector", "sync", "run", "prompt", "prompt2", "actor")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    t = ids["tenant"]
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO tenant(id,slug,name,status) VALUES(:t,:slug,'v','active')"), {"t": t, "slug": f"v-{t.hex[:8]}"})
        await session.execute(
            text("INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,verified_at)"
                 " VALUES(:s,:t,'TheCalcHive','https://thecalchive.com','thecalchive.com','observe','active',now())"),
            {"s": ids["site"], "t": t},
        )
        await session.execute(
            text("INSERT INTO connector(id,tenant_id,site_id,type,status) VALUES(:c,:t,:s,'google_search_console','active')"),
            {"c": ids["connector"], "t": t, "s": ids["site"]},
        )
        await session.execute(
            text("INSERT INTO connector_sync(id,tenant_id,connector_id,kind,idempotency_key,range_start,range_end,requested_by)"
                 " VALUES(:y,:t,:c,'incremental',:k,'2026-09-01','2026-10-01',:a)"),
            {"y": ids["sync"], "t": t, "c": ids["connector"], "k": f"sync-{t.hex}", "a": ids["actor"]},
        )

        async def term(value: str) -> str:
            query_hash = hashlib.sha256(value.encode()).hexdigest()
            aad = query_aad(t, ids["site"], "v1")
            nonce = os.urandom(12)
            await session.execute(
                text("INSERT INTO search_query(tenant_id,site_id,query_hash,ciphertext,nonce,aad_hash,key_version,term_length,token_count,is_question)"
                     " VALUES(:t,:s,:h,:c,:n,:a,'v1',:l,1,false)"),
                {"t": t, "s": ids["site"], "h": query_hash, "c": AESGCM(KEY).encrypt(nonce, value.encode(), aad),
                 "n": nonce, "a": hashlib.sha256(aad).hexdigest(), "l": len(value)},
            )
            return query_hash

        async def metric(day: date, query_hash: str, clicks: float, impressions: float) -> None:
            url = f"https://thecalchive.com/{query_hash[:6]}"
            await session.execute(
                text("INSERT INTO search_metric(tenant_id,site_id,metric_date,query_hash,page_url,page_url_hash,clicks,impressions,ctr,position,source_sync_id)"
                     " VALUES(:t,:s,:d,:h,:u,:uh,:c,:i,:ctr,5,:y)"),
                {"t": t, "s": ids["site"], "d": day, "h": query_hash, "u": url, "uh": hashlib.sha256(url.encode()).hexdigest(),
                 "c": clicks, "i": impressions, "ctr": clicks / impressions if impressions else 0, "y": ids["sync"]},
            )

        branded = await term("calc hive emi")
        plain = await term("emi calculator")
        answered = await term("what is a good bmi")
        await metric(LAST_WEEK, branded, 3, 10)
        await metric(LAST_WEEK, plain, 1, 40)
        await metric(LAST_WEEK, answered, 0, 25)
        await metric(THIS_WEEK, plain, 0, 2)  # Too rare to call answered.

        for prompt in ("prompt", "prompt2"):
            await session.execute(
                text("INSERT INTO ai_citation_prompt(id,tenant_id,site_id,prompt,source,created_by)"
                     " VALUES(:p,:t,:s,:q,'manual',:a)"),
                {"p": ids[prompt], "t": t, "s": ids["site"], "q": f"How is emi calculated {prompt}?", "a": ids["actor"]},
            )
        await session.execute(
            text("INSERT INTO ai_citation_run(id,tenant_id,site_id,finished_at) VALUES(:r,:t,:s,:f)"),
            {"r": ids["run"], "t": t, "s": ids["site"], "f": datetime(2026, 9, 30, tzinfo=UTC)},
        )
        # A run asks each engine each question once.
        for prompt, provider, cited, mentioned, competitors, state in (
            ("prompt", "openai", True, True, [], "answered"),
            ("prompt", "anthropic", False, True, ["rival.example"], "answered"),
            ("prompt", "perplexity", False, False, ["rival.example"], "answered"),
            ("prompt2", "openai", False, False, [], "failed"),
        ):
            await session.execute(
                text("INSERT INTO ai_citation_observation(tenant_id,site_id,run_id,prompt_id,prompt,provider,model,status,"
                     "site_cited,site_mentioned,competitor_hosts) VALUES(:t,:s,:r,:p,'q',:pr,'m',:st,:c,:m,CAST(:h AS jsonb))"),
                {"pr": provider, "t": t, "s": ids["site"], "r": ids["run"], "p": ids[prompt], "st": state, "c": cited, "m": mentioned,
                 "h": str(competitors).replace("'", '"')},
            )
    yield ids
    async with factory() as session, session.begin():
        for table in ("audit_event", "ai_citation_observation", "ai_citation_run", "ai_citation_prompt", "search_metric", "search_query",
                      "connector_sync", "connector", "site"):
            await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": t})
        await session.execute(text("DELETE FROM tenant WHERE id=:t"), {"t": t})


def ctx(ids, role=Role.OWNER, tenant=None):
    return TenantContext(tenant_id=tenant or ids["tenant"], actor_id=ids["actor"], role=role, trace_id="t")


async def test_the_trend_splits_branded_demand_and_counts_answered_queries(tenant_session_factory, site):
    async with tenant_session_factory(site["tenant"]) as session:
        trend = await VisibilityService(session, ctx(site), encryption_key=KEY).trend(site["site"], weeks=2, today=TODAY)

    assert [row.week_start for row in trend.weeks] == [LAST_WEEK, THIS_WEEK]
    last = trend.weeks[0]
    assert (last.clicks, last.impressions) == (4, 75)
    assert (last.branded_clicks, last.branded_impressions) == (3, 10)
    assert (last.zero_click_queries, last.zero_click_impressions) == (1, 25)
    assert trend.weeks[1].zero_click_queries == 0
    assert trend.brand_terms == ["thecalchive", "calchive"] and trend.brand_terms_derived
    assert trend.search_connected and trend.branded_available and not trend.analytics_connected
    assert last.direct_sessions is None
    assert trend.sparse

    [run] = trend.citation_runs
    # The failed answer is not in the sample.
    assert (run.answers, run.named, run.cited, run.competitor_cited) == (3, 2, 1, 2)


async def test_without_the_key_the_branded_split_is_unknown_not_zero(tenant_session_factory, site):
    async with tenant_session_factory(site["tenant"]) as session:
        trend = await VisibilityService(session, ctx(site)).trend(site["site"], weeks=2, today=TODAY)
    assert not trend.branded_available
    assert trend.weeks[0].branded_impressions is None
    assert trend.notes == ["branded_split_unavailable"]


async def test_edited_brand_terms_change_the_split_and_are_audited(tenant_session_factory, site):
    async with tenant_session_factory(site["tenant"]) as session:
        service = VisibilityService(session, ctx(site), encryption_key=KEY)
        assert await service.set_brand_terms(site["site"], ["EMI Calc"]) == ["emicalc"]
        trend = await service.trend(site["site"], weeks=2, today=TODAY)
        assert trend.weeks[0].branded_impressions == 40
        assert not trend.brand_terms_derived
        audited = await session.scalar(text("SELECT count(*) FROM audit_event WHERE action='site.brand_terms_updated'"))
        assert audited == 1
        # Empty goes back to the derived terms.
        assert await service.set_brand_terms(site["site"], []) == ["thecalchive", "calchive"]


async def test_a_viewer_cannot_edit_brand_terms(tenant_session_factory, site):
    async with tenant_session_factory(site["tenant"]) as session:
        with pytest.raises(HTTPException) as refused:
            await VisibilityService(session, ctx(site, Role.VIEWER)).set_brand_terms(site["site"], ["x" * 5])
    assert refused.value.status_code == 403


async def test_another_tenant_cannot_read_the_trend(tenant_session_factory, site):
    other = uuid4()
    async with tenant_session_factory(other) as session:
        with pytest.raises(HTTPException) as refused:
            await VisibilityService(session, ctx(site, tenant=other), encryption_key=KEY).trend(site["site"], today=TODAY)
    assert refused.value.status_code == 404
