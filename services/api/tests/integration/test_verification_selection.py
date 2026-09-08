"""What the verifier picks up, against PostgreSQL.

The query is the whole of the sweep's judgement about *which* changes to go and
look at, and a clause appearing in a string cannot show whether it returns one
row or none. These cases seed deployments and count what comes back.

The case that matters most is the last one: a verification that already
succeeded is never revisited. The change was observed on the live page, which
is a fact about a moment; letting a later unrelated edit to the same file
retract it would turn a record of what happened into a rolling opinion.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.deployment_verification import RETRY_WINDOW_DAYS, unverified
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SCORING_VERSION_ID = UUID("019d0000-0000-7000-8000-000000000090")
HOST = "verify.example"


async def _seed(session, ids: dict[str, UUID]) -> None:
    await session.execute(
        text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'verify','active')"),
        {"id": ids["tenant_id"], "slug": f"verify-{ids['tenant_id'].hex[:8]}"},
    )
    await session.execute(
        text(
            "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,"
            f"verified_at) VALUES(:id,:tenant_id,'v','https://{HOST}','{HOST}',"
            "'recommend','active',now())"
        ),
        {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
    )


async def _deployed(
    session,
    ids: dict[str, UUID],
    path: str,
    *,
    verification: str | None = None,
    with_receipt: bool = True,
    proposal_status: str = "deployed",
    deployed_days_ago: float = 1 / 24,
) -> UUID:
    """A page, a proposal in `proposal_status`, and optionally its receipt."""
    url = f"https://{HOST}{path}"
    page_id, proposal_id = uuid4(), uuid4()
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
    opportunity_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,status,impact,"
            "confidence,urgency,effort,risk,score,scoring_version_id,evidence_refs,"
            "fingerprint) VALUES(:id,:tenant_id,:site_id,:page_id,'Fix the heading','open',"
            "0.8,0.9,0.7,0.3,'low',66,:scoring_version_id,'{}'::jsonb,:fingerprint)"
        ),
        {
            "id": opportunity_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "page_id": page_id,
            "scoring_version_id": SCORING_VERSION_ID,
            "fingerprint": sha256(opportunity_id.bytes).hexdigest(),
        },
    )
    await session.execute(
        text(
            "INSERT INTO proposal(id,tenant_id,site_id,opportunity_id,page_id,author_id,title,"
            "rationale,"
            "target_type,target_path,before_content,after_content,diff_unified,base_hash,"
            "proposal_hash,risk,status,validations_json,policy_evaluation_json,evidence_refs,"
            "expires_at) VALUES(:id,:tenant_id,:site_id,:opportunity_id,:page_id,:author_id,"
            "'Set the H1',"
            "'Because the heading is shared','github_file',:target,'<h1>a</h1>','<h1>b</h1>',"
            "'+ <h1>b</h1>',:h,:h,'medium',:status,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,"
            "now() + interval '14 days')"
        ),
        {
            "id": proposal_id,
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "opportunity_id": opportunity_id,
            "page_id": page_id,
            "author_id": ids["actor_id"],
            "target": f"WordKit{path}.html",
            "status": proposal_status,
            "h": sha256(proposal_id.bytes).hexdigest(),
        },
    )
    if with_receipt:
        receipt_id = uuid4()
        await session.execute(
            text(
                "INSERT INTO deployment_receipt(id,tenant_id,site_id,proposal_id,connector_type,"
                "idempotency_key,external_ref,manifest_json,status,deployed_at)"
                " VALUES(:id,:tenant_id,:site_id,:proposal_id,'github',:key,"
                "'https://github.com/o/r/pull/1','{}'::jsonb,'applied',:at)"
            ),
            {
                "id": receipt_id,
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "proposal_id": proposal_id,
                "key": f"deploy-{proposal_id}",
                "at": datetime.now(UTC) - timedelta(days=deployed_days_ago),
            },
        )
        if verification is not None:
            await session.execute(
                text(
                    "INSERT INTO post_deploy_verification(id,tenant_id,site_id,proposal_id,"
                    "deployment_receipt_id,page_id,status,expected_pattern,http_status,notes)"
                    " VALUES(:id,:tenant_id,:site_id,:proposal_id,:receipt_id,:page_id,:status,"
                    "'<h1>b</h1>',200,'seeded')"
                ),
                {
                    "id": uuid4(),
                    "tenant_id": ids["tenant_id"],
                    "site_id": ids["site_id"],
                    "proposal_id": proposal_id,
                    "receipt_id": receipt_id,
                    "page_id": page_id,
                    "status": verification,
                },
            )
    return proposal_id


async def _teardown(session, tenant_id: UUID) -> None:
    for table in (
        "measurement_series", "post_deploy_verification", "deployment_receipt",
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
    ids: dict[str, UUID] = {name: uuid4() for name in ("tenant_id", "site_id", "actor_id")}
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await _seed(session, ids)
    yield ids, factory
    async with factory() as session, session.begin():
        await _teardown(session, ids["tenant_id"])


async def picked_up(engine, ids: dict[str, UUID]) -> list[UUID]:
    async with engine.connect() as connection:
        rows = await unverified(connection, 50)
    return [row.proposal_id for row in rows if row.tenant_id == ids["tenant_id"]]


async def test_a_deployed_change_nobody_has_checked_is_picked_up(engine, ground) -> None:
    ids, factory = ground
    async with factory() as session, session.begin():
        proposal_id = await _deployed(session, ids, "/rhyme-tool")

    assert await picked_up(engine, ids) == [proposal_id]


async def test_a_change_already_seen_on_the_page_is_never_revisited(engine, ground) -> None:
    """A verified deployment is a fact about a moment, not a standing opinion.

    Re-reading it would let an unrelated later edit to the same file retract an
    observation that was true when it was made.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        await _deployed(session, ids, "/rhyme-tool", verification="verified")

    assert await picked_up(engine, ids) == []


async def test_a_change_looked_for_and_not_found_is_looked_for_again(engine, ground) -> None:
    """The usual reason for `failed` is a pull request nobody has merged yet.

    If that were terminal, a change merged an hour later would never be
    verified and could never be measured.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        proposal_id = await _deployed(session, ids, "/wordle-tool", verification="failed")

    assert await picked_up(engine, ids) == [proposal_id]


async def test_a_proposal_without_a_receipt_is_not_picked_up(engine, ground) -> None:
    """Nothing was deployed, so there is nothing on a live page to look for."""
    ids, factory = ground
    async with factory() as session, session.begin():
        await _deployed(session, ids, "/hangman-tool", with_receipt=False)

    assert await picked_up(engine, ids) == []


async def test_only_deployed_proposals_are_picked_up(engine, ground) -> None:
    ids, factory = ground
    async with factory() as session, session.begin():
        await _deployed(session, ids, "/anagram-tool", proposal_status="approved")
        live = await _deployed(session, ids, "/scrabble-tool")

    assert await picked_up(engine, ids) == [live]


async def test_the_page_url_and_its_site_host_come_back_together(engine, ground) -> None:
    """`belongs_to_site` needs both, and getting them from one row is the point.

    Fetching the URL from one query and the host it is checked against from
    another is how the two come to disagree.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        await _deployed(session, ids, "/word-search")

    async with engine.connect() as connection:
        rows = [r for r in await unverified(connection, 50) if r.tenant_id == ids["tenant_id"]]
    assert len(rows) == 1
    assert rows[0].url == f"https://{HOST}/word-search"
    assert rows[0].host == HOST


async def test_an_old_failure_is_left_alone(engine, ground) -> None:
    """A pull request unmerged for a fortnight is not about to be merged.

    The other reason for failing -- superseded by a later change -- is
    permanent. Without a bound the sweep re-fetches these pages four times an
    hour for ever to re-learn something already written down; on 2026-09-08
    that was sixteen thecalchive.com pages.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        await _deployed(
            session,
            ids,
            "/superseded-tool",
            verification="failed",
            deployed_days_ago=RETRY_WINDOW_DAYS + 1,
        )

    assert await picked_up(engine, ids) == []


async def test_an_old_deployment_nobody_ever_checked_is_still_checked(engine, ground) -> None:
    """The bound is on retrying, not on looking.

    Enabling this sweep has to reach the whole backlog once, or every
    deployment made before it existed would be invisible to it for ever --
    which is the exact gap it was built to close.
    """
    ids, factory = ground
    async with factory() as session, session.begin():
        proposal_id = await _deployed(
            session, ids, "/ancient-tool", deployed_days_ago=RETRY_WINDOW_DAYS * 10
        )

    assert await picked_up(engine, ids) == [proposal_id]
