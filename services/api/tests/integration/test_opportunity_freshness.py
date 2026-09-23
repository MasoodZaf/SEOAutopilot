"""The advisory queue is measured against the latest analyzed crawl.

A crawl closes the opportunities on pages it re-reads, but it can say nothing
about a page it did not reach. On a site larger than the crawl's page limit,
opportunities on the unreached pages stayed open with evidence from an older
crawl and ranked beside fresh ones -- so issues the site had already fixed
headed the queue (codearc.net, 2026-09-23), and calibration refused to build a
review set at all, because it rejects stale evidence.

These cases seed two crawls and check what the default queue, the `all` scope,
the not-rechecked count and calibration each see, on the application role.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role, TenantContext
from app.services.calibrations import CalibrationService
from app.services.opportunities import OpportunityService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SCORING_VERSION_ID = UUID("019d0000-0000-7000-8000-000000000090")


async def _crawl(session, ids, key: str, age: timedelta) -> None:
    await session.execute(
        text(
            "INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status,"
            "created_at) VALUES(:id,:tenant_id,:site_id,:actor,'{}'::jsonb,'partial',"
            "CAST(:at AS timestamptz))"
        ),
        {
            "id": ids[key],
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "actor": ids["actor_id"],
            "at": datetime.now(UTC) - age,
        },
    )
    await session.execute(
        text(
            "INSERT INTO analysis_run(id,tenant_id,site_id,crawl_job_id,agent_version,"
            "request_hash,status,created_at) VALUES(:id,:tenant_id,:site_id,:crawl_id,"
            "'test-v1',:hash,'completed',CAST(:at AS timestamptz))"
        ),
        {
            "id": ids[f"{key}_analysis"],
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "crawl_id": ids[key],
            "hash": sha256(ids[key].bytes).hexdigest(),
            "at": datetime.now(UTC) - age,
        },
    )


async def _opportunity(session, ids, name: str, crawl_key: str | None, score: float) -> None:
    """One page, one observation from `crawl_key`, one finding and its opportunity."""
    url = f"https://fresh.example/{name}"
    page_id, observation_id, finding_id = uuid4(), uuid4(), uuid4()
    crawl_id = ids[crawl_key or "new_crawl"]
    ids[name] = uuid4()
    await session.execute(
        text(
            "INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash)"
            " VALUES(:id,:tenant_id,:site_id,:url,:url_hash)"
        ),
        {
            "id": page_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "url": url,
            "url_hash": sha256(url.encode()).hexdigest(),
        },
    )
    await session.execute(
        text(
            "INSERT INTO page_observation(id,tenant_id,page_id,crawl_job_id,http_status,"
            "final_url,title,meta_description,h1_json,word_count,content_hash,rendered)"
            " VALUES(:id,:tenant_id,:page_id,:crawl_id,200,:url,'A title','A description',"
            "'[]'::jsonb,300,:hash,false)"
        ),
        {
            "id": observation_id,
            "tenant_id": ids["tenant_id"],
            "page_id": page_id,
            "crawl_id": crawl_id,
            "url": url,
            "hash": sha256(url.encode()).hexdigest(),
        },
    )
    await session.execute(
        text(
            "INSERT INTO finding(id,tenant_id,site_id,page_id,analysis_run_id,"
            "scoring_version_id,rule_key,severity,evidence_refs,summary,confidence,fingerprint)"
            " VALUES(:id,:tenant_id,:site_id,:page_id,:analysis_id,:version,:rule,'medium',"
            "'{}'::jsonb,'a finding',0.9,:fingerprint)"
        ),
        {
            "id": finding_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "page_id": page_id,
            "analysis_id": ids[f"{crawl_key or 'new_crawl'}_analysis"],
            "version": SCORING_VERSION_ID,
            "rule": f"rule.{name}",
            "fingerprint": sha256(finding_id.bytes).hexdigest(),
        },
    )
    evidence = (
        f'{{"crawl_id":"{crawl_id}","observation_id":"{observation_id}"}}'
        if crawl_key
        else f'{{"observation_id":"{observation_id}"}}'
    )
    await session.execute(
        text(
            "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,impact,confidence,"
            "urgency,effort,risk,score,scoring_version_id,evidence_refs,fingerprint)"
            " VALUES(:id,:tenant_id,:site_id,:page_id,:title,0.8,0.9,0.7,0.3,'low',:score,"
            ":version,CAST(:evidence AS jsonb),:fingerprint)"
        ),
        {
            "id": ids[name],
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "page_id": page_id,
            "title": f"Fix {name}",
            "score": score,
            "version": SCORING_VERSION_ID,
            "evidence": evidence,
            "fingerprint": sha256(ids[name].bytes).hexdigest(),
        },
    )
    await session.execute(
        text(
            "INSERT INTO opportunity_finding(tenant_id,opportunity_id,finding_id)"
            " VALUES(:tenant_id,:opportunity_id,:finding_id)"
        ),
        {"tenant_id": ids["tenant_id"], "opportunity_id": ids[name], "finding_id": finding_id},
    )


@pytest_asyncio.fixture
async def two_crawls(engine):
    ids: dict[str, UUID] = {
        name: uuid4()
        for name in (
            "tenant_id", "site_id", "actor_id",
            "old_crawl", "old_crawl_analysis", "new_crawl", "new_crawl_analysis",
        )
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'fresh','active')"),
            {"id": ids["tenant_id"], "slug": f"fresh-{ids['tenant_id'].hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,"
                "mode,status,verified_at) VALUES(:id,:tenant_id,'f','https://fresh.example',"
                "'fresh.example','observe','active',now())"
            ),
            {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
        )
        await _crawl(session, ids, "old_crawl", timedelta(days=2))
        await _crawl(session, ids, "new_crawl", timedelta(hours=1))
        # Confirmed by the latest crawl.
        await _opportunity(session, ids, "confirmed", "new_crawl", 50)
        # Found by the old crawl on a page the latest one did not reach. It
        # scores highest, so an unscoped queue would put it first.
        await _opportunity(session, ids, "unreached", "old_crawl", 95)
        # Evidence with no crawl recorded at all.
        await _opportunity(session, ids, "unattributed", None, 90)
    yield ids
    async with factory() as session, session.begin():
        for table in (
            "calibration_item", "calibration_run", "opportunity_finding", "opportunity",
            "finding", "analysis_run", "page_observation", "page", "crawl_job",
            "outbox_event", "audit_event", "site",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"),
                {"tenant_id": ids["tenant_id"]},
            )
        await session.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": ids["tenant_id"]})


def _context(ids) -> TenantContext:
    return TenantContext(
        tenant_id=ids["tenant_id"], actor_id=ids["actor_id"], role=Role.OWNER,
        trace_id="integration",
    )


async def test_default_queue_holds_only_what_the_latest_crawl_confirmed(
    tenant_session_factory, two_crawls
) -> None:
    async with tenant_session_factory(two_crawls["tenant_id"]) as session:
        service = OpportunityService(session, _context(two_crawls))

        current = await service.list_top(two_crawls["site_id"], 20, "open")

        assert current is not None
        assert [item.id for item in current] == [two_crawls["confirmed"]]


async def test_all_scope_still_lists_what_was_not_rechecked(
    tenant_session_factory, two_crawls
) -> None:
    async with tenant_session_factory(two_crawls["tenant_id"]) as session:
        service = OpportunityService(session, _context(two_crawls))

        everything = await service.list_top(two_crawls["site_id"], 20, "open", scope="all")

        assert everything is not None
        assert {item.id for item in everything} == {
            two_crawls["confirmed"], two_crawls["unreached"], two_crawls["unattributed"],
        }


async def test_not_rechecked_count_names_the_gap(tenant_session_factory, two_crawls) -> None:
    async with tenant_session_factory(two_crawls["tenant_id"]) as session:
        service = OpportunityService(session, _context(two_crawls))

        latest = await service.latest_analyzed_crawl(two_crawls["site_id"])
        assert latest is not None
        assert latest == two_crawls["new_crawl"]
        assert await service.not_rechecked_count(two_crawls["site_id"], latest) == 2


async def test_scope_does_not_hide_work_in_progress(tenant_session_factory, two_crawls) -> None:
    """A shortlisted item is a person's decision; a crawl must not make it vanish."""
    async with tenant_session_factory(two_crawls["tenant_id"]) as session:
        await session.execute(
            text("UPDATE opportunity SET status='shortlisted' WHERE id=:id"),
            {"id": two_crawls["unreached"]},
        )
        service = OpportunityService(session, _context(two_crawls))

        shortlisted = await service.list_top(two_crawls["site_id"], 20, "shortlisted")

        assert shortlisted is not None
        assert [item.id for item in shortlisted] == [two_crawls["unreached"]]


async def test_calibration_is_no_longer_blocked_by_an_unreached_page(
    tenant_session_factory, two_crawls
) -> None:
    async with tenant_session_factory(two_crawls["tenant_id"]) as session:
        run = await CalibrationService(session, _context(two_crawls)).create_run(
            two_crawls["site_id"], target_size=20, idempotency_key=str(uuid4())
        )

        items = run["items"]
        assert isinstance(items, list)
        assert [str(item["opportunity_id"]) for item in items] == [str(two_crawls["confirmed"])]
