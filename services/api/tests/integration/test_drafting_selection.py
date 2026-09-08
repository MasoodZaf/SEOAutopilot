"""What the drafting sweep selects, against PostgreSQL.

The unit tests for this sweep assert that certain clauses appear in the query
text. That is worth having and it is not evidence: it cannot tell whether the
query returns one row or two, which is the only question that matters here.

It matters because of what shipped on 2026-09-08. Six wordkitapp.com pages
carried two open opportunities each -- `h1.duplicate_across_site` and
`content.title_h1_mismatch` -- and `h1_repair` answers both with the identical
one-line edit. The sweep drafted the second one on top of a proposal that was
already open, and two pull requests changing one line to the same thing is not
a thing anybody asked for.

Both halves of that are tested here, because they are genuinely separate. The
`NOT EXISTS` guard only settles sweeps after the first; within a single sweep
both rows are selected before either is written, so the batch has to hold the
rule itself.
"""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.proposal_drafting import draftable
from app.services.proposal_drafts import REPAIRABLE_RULES
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SCORING_VERSION_ID = UUID("019d0000-0000-7000-8000-000000000090")
RULES = sorted(REPAIRABLE_RULES)


async def _page(session, ids: dict[str, UUID], path: str) -> UUID:
    url = f"https://select.example{path}"
    page_id = uuid4()
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
    return page_id


async def _opportunity(
    session, ids: dict[str, UUID], page_id: UUID | None, rule_key: str, score: int
) -> UUID:
    """An open opportunity on this page, joined to a finding carrying `rule_key`."""
    finding_id, opportunity_id = uuid4(), uuid4()
    await session.execute(
        text(
            "INSERT INTO finding(id,tenant_id,site_id,page_id,analysis_run_id,"
            "scoring_version_id,rule_key,severity,evidence_refs,summary,confidence,"
            "fingerprint) VALUES(:id,:tenant_id,:site_id,:page_id,:analysis_id,"
            ":scoring_version_id,:rule_key,'medium','{}'::jsonb,'a finding',0.9,:fingerprint)"
        ),
        {
            "id": finding_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "page_id": page_id,
            "analysis_id": ids["analysis_id"],
            "scoring_version_id": SCORING_VERSION_ID,
            "rule_key": rule_key,
            "fingerprint": sha256(finding_id.bytes).hexdigest(),
        },
    )
    await session.execute(
        text(
            "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,status,impact,"
            "confidence,urgency,effort,risk,score,scoring_version_id,evidence_refs,"
            "fingerprint) VALUES(:id,:tenant_id,:site_id,:page_id,:title,'open',0.8,0.9,"
            "0.7,0.3,'low',:score,:scoring_version_id,'{}'::jsonb,:fingerprint)"
        ),
        {
            "id": opportunity_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "page_id": page_id,
            "title": f"Fix {rule_key}",
            "score": score,
            "scoring_version_id": SCORING_VERSION_ID,
            "fingerprint": sha256(opportunity_id.bytes).hexdigest(),
        },
    )
    await session.execute(
        text(
            "INSERT INTO opportunity_finding(tenant_id,opportunity_id,finding_id)"
            " VALUES(:tenant_id,:opportunity_id,:finding_id)"
        ),
        {
            "tenant_id": ids["tenant_id"],
            "opportunity_id": opportunity_id,
            "finding_id": finding_id,
        },
    )
    return opportunity_id


async def _live_proposal(session, ids: dict[str, UUID], page_id: UUID, opportunity_id: UUID):
    """A proposal in a status the sweep must treat as already covering this page."""
    await session.execute(
        text(
            "INSERT INTO proposal(id,tenant_id,site_id,opportunity_id,page_id,author_id,title,"
            "rationale,target_type,target_path,before_content,after_content,diff_unified,"
            "base_hash,proposal_hash,risk,status,validations_json,policy_evaluation_json,"
            "evidence_refs,expires_at) VALUES(:id,:tenant_id,:site_id,:opportunity_id,:page_id,"
            ":author_id,'Set the H1','Because the heading is shared','github_file','WordKit/x.html','<h1>a</h1>','<h1>b</h1>','@@ -1 +1 @@',:h,:h,'medium','review_required',"
            "'{}'::jsonb,'{}'::jsonb,'{}'::jsonb, now() + interval '14 days')"
        ),
        {
            "id": uuid4(),
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "opportunity_id": opportunity_id,
            "page_id": page_id,
            "author_id": ids["actor_id"],
            "h": sha256(b"x").hexdigest(),
        },
    )


async def _seed(session, ids: dict[str, UUID]) -> None:
    await session.execute(
        text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'select','active')"),
        {"id": ids["tenant_id"], "slug": f"select-{ids['tenant_id'].hex[:8]}"},
    )
    await session.execute(
        text(
            "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,"
            "verified_at) VALUES(:id,:tenant_id,'s','https://select.example','select.example',"
            "'recommend','active',now())"
        ),
        {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
    )
    # Somewhere to write. Without an active repository connector nothing is
    # draftable at all and every assertion below would pass vacuously.
    await session.execute(
        text(
            "INSERT INTO connector(id,tenant_id,site_id,type,status,provider_key,config_json)"
            " VALUES(:id,:tenant_id,:site_id,'github_repository','active','github_app',"
            "'{}'::jsonb)"
        ),
        {"id": uuid4(), "tenant_id": ids["tenant_id"], "site_id": ids["site_id"]},
    )
    await session.execute(
        text(
            "INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status)"
            " VALUES(:id,:tenant_id,:site_id,:actor,'{}'::jsonb,'completed')"
        ),
        {
            "id": ids["crawl_id"],
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "actor": ids["actor_id"],
        },
    )
    await session.execute(
        text(
            "INSERT INTO analysis_run(id,tenant_id,site_id,crawl_job_id,agent_version,"
            "request_hash,status)"
            " VALUES(:id,:tenant_id,:site_id,:crawl_id,'test-v1',:request_hash,'completed')"
        ),
        {
            "id": ids["analysis_id"],
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "crawl_id": ids["crawl_id"],
            "request_hash": sha256(b"select").hexdigest(),
        },
    )


async def _teardown(session, tenant_id: UUID) -> None:
    for table in (
        "proposal_approval", "proposal", "opportunity_finding", "opportunity", "finding",
        "page_score", "analysis_run", "page_observation", "page", "crawl_job", "connector",
        "outbox_event", "audit_event", "site",
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
        )
    await session.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})


@pytest_asyncio.fixture
async def ground(engine):
    ids: dict[str, UUID] = {
        name: uuid4() for name in ("tenant_id", "site_id", "crawl_id", "analysis_id", "actor_id")
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await _seed(session, ids)
    yield ids, factory
    async with factory() as session, session.begin():
        await _teardown(session, ids["tenant_id"])


async def selected(engine, ids: dict[str, UUID]) -> list[UUID]:
    """Candidate opportunity ids for this tenant, in the order the sweep sees them."""
    async with engine.connect() as connection:
        rows = await draftable(connection, RULES, 20)
    return [row.opportunity_id for row in rows if row.tenant_id == ids["tenant_id"]]


async def test_one_page_with_two_repairable_opportunities_yields_one_candidate(
    engine, ground
) -> None:
    """The defect, in its original form.

    Both rules are repairable and both resolve to the same edit on the same
    file. Selecting both drafts that edit twice in a single sweep, before
    either write has happened for the other to see.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        page_id = await _page(session, ids, "/anagram-tool")
        low = await _opportunity(session, ids, page_id, "content.title_h1_mismatch", 24)
        high = await _opportunity(session, ids, page_id, "h1.duplicate_across_site", 66)

    candidates = await selected(engine, ids)
    assert candidates == [high], "expected exactly the higher-scoring opportunity for the page"
    assert low not in candidates


async def test_a_live_proposal_on_the_page_covers_its_other_opportunity(
    engine, ground
) -> None:
    """The form the defect actually took in production.

    Twelve proposals were open, one per page. The sweep drafted the *other*
    opportunity on six of those pages, because the guard read opportunity_id
    and the collision was on the page.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        page_id = await _page(session, ids, "/rhyme-tool")
        covered = await _opportunity(session, ids, page_id, "h1.duplicate_across_site", 66)
        await _opportunity(session, ids, page_id, "content.title_h1_mismatch", 24)
        await _live_proposal(session, ids, page_id, covered)

    assert await selected(engine, ids) == []


async def test_a_page_nobody_is_proposing_against_is_still_drafted(engine, ground) -> None:
    """The guards must not swallow the work they exist to order.

    A test that only proves things are excluded passes just as well on a query
    that returns nothing at all.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        busy = await _page(session, ids, "/wordle-tool")
        taken = await _opportunity(session, ids, busy, "h1.duplicate_across_site", 66)
        await _live_proposal(session, ids, busy, taken)

        free = await _page(session, ids, "/scrabble-tool")
        open_one = await _opportunity(session, ids, free, "h1.duplicate_across_site", 70)

    assert await selected(engine, ids) == [open_one]
