"""The calibration workflow, against PostgreSQL.

Calibration is where the product's precision claim comes from: a frozen set of
top opportunities, labelled by a human, from which per-rule precision and
actionability are computed. Everything downstream of "this tool finds real
problems" rests on it.

Its only test asserted that a viewer gets a 403. Every line after the role
check -- evidence readiness, the idempotency and open-run checks, and item
construction from findings, pages and observations -- had never been executed.
`create_run` reads six tables and joins opportunities to findings and
observations through `evidence_refs`; a mocked session returns whatever the test
hands it for each of those, so it can only ever confirm the shape the test
already assumed.

These cases seed a real evidence chain and run the real service on the real
`seo_autopilot_app` role.
"""

import asyncio
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.schemas import CalibrationReviewCreate
from app.core.context import Role, TenantContext
from app.services.calibrations import CalibrationService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SCORING_VERSION_ID = UUID("019d0000-0000-7000-8000-000000000090")


async def _seed(session, ids: dict[str, UUID], *, crawl_status: str = "completed") -> None:
    """One tenant with three scored opportunities, each with its own evidence."""
    await session.execute(
        text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'cal','active')"),
        {"id": ids["tenant_id"], "slug": f"cal-{ids['tenant_id'].hex[:8]}"},
    )
    await session.execute(
        text(
            "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,"
            "mode,status,verified_at)"
            " VALUES(:id,:tenant_id,'c','https://cal.example','cal.example',"
            "'observe','active',now())"
        ),
        {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
    )
    await session.execute(
        text(
            "INSERT INTO crawl_job(id,tenant_id,site_id,requested_by,config_snapshot,status)"
            " VALUES(:id,:tenant_id,:site_id,:actor,'{}'::jsonb,:status)"
        ),
        {
            "id": ids["crawl_id"],
            "tenant_id": ids["tenant_id"],
            "site_id": ids["site_id"],
            "actor": ids["actor_id"],
            "status": crawl_status,
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
            "request_hash": sha256(b"analysis").hexdigest(),
        },
    )
    # Three distinct pages so the diversity ranking has something to rank, and
    # distinct rule keys so it cannot collapse them into one round.
    for index, rule_key in enumerate(("h1.missing", "title.length", "description.length")):
        url = f"https://cal.example/page-{index}"
        page_id, observation_id, finding_id, opportunity_id = (
            uuid4(), uuid4(), uuid4(), uuid4(),
        )
        ids[f"page_{index}"] = page_id
        ids[f"opportunity_{index}"] = opportunity_id
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
                "'[]'::jsonb,:words,:hash,false)"
            ),
            {
                "id": observation_id,
                "tenant_id": ids["tenant_id"],
                "page_id": page_id,
                "crawl_id": ids["crawl_id"],
                "url": url,
                "words": 300 + index,
                "hash": sha256(url.encode()).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO finding(id,tenant_id,site_id,page_id,analysis_run_id,"
                "scoring_version_id,rule_key,severity,evidence_refs,summary,confidence,"
                "fingerprint)"
                " VALUES(:id,:tenant_id,:site_id,:page_id,:analysis_id,:scoring_version_id,"
                ":rule_key,'medium','{}'::jsonb,'a finding',0.9,:fingerprint)"
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
                "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,impact,confidence,"
                "urgency,effort,risk,score,scoring_version_id,evidence_refs,fingerprint)"
                " VALUES(:id,:tenant_id,:site_id,:page_id,:title,0.8,0.9,0.7,0.3,'low',:score,"
                ":scoring_version_id,CAST(:evidence AS jsonb),:fingerprint)"
            ),
            {
                "id": opportunity_id,
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "page_id": page_id,
                "title": f"Fix {rule_key}",
                "score": 90 - index,
                "scoring_version_id": SCORING_VERSION_ID,
                "evidence": (
                    f'{{"crawl_id":"{ids["crawl_id"]}","observation_id":"{observation_id}"}}'
                ),
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


async def _teardown(session, tenant_id: UUID) -> None:
    for table in (
        "calibration_review", "calibration_item", "calibration_run", "opportunity_finding",
        "opportunity", "finding", "page_score", "analysis_run", "page_observation", "page",
        "crawl_job", "outbox_event", "audit_event", "site",
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
        )
    await session.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})


@pytest_asyncio.fixture
async def evidence(engine):
    ids: dict[str, UUID] = {
        name: uuid4()
        for name in ("tenant_id", "site_id", "crawl_id", "analysis_id", "actor_id")
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await _seed(session, ids)
    yield ids
    async with factory() as session, session.begin():
        await _teardown(session, ids["tenant_id"])


@pytest_asyncio.fixture
async def stale_evidence(engine):
    """The same chain, but the crawl never finished."""
    ids: dict[str, UUID] = {
        name: uuid4()
        for name in ("tenant_id", "site_id", "crawl_id", "analysis_id", "actor_id")
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await _seed(session, ids, crawl_status="running")
    yield ids
    async with factory() as session, session.begin():
        await _teardown(session, ids["tenant_id"])


def calibration(session, ids: dict[str, UUID], role: Role = Role.OWNER) -> CalibrationService:
    return CalibrationService(
        session,
        TenantContext(
            tenant_id=ids["tenant_id"],
            actor_id=ids["actor_id"],
            role=role,
            trace_id="integration",
        ),
    )


def scoped(app_engine, ids: dict[str, UUID]):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    class _Scoped:
        async def __aenter__(self):
            self._session = factory()
            await self._session.__aenter__()
            self._transaction = self._session.begin()
            await self._transaction.__aenter__()
            await self._session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(ids["tenant_id"])},
            )
            return self._session

        async def __aexit__(self, *exc):
            await self._session.rollback()
            await self._session.__aexit__(None, None, None)

    return _Scoped()


async def test_a_run_freezes_the_top_opportunities_with_their_evidence(
    app_engine, evidence
) -> None:
    """The snapshot is the point: a label is only meaningful against fixed evidence."""
    async with scoped(app_engine, evidence) as session:
        run = await calibration(session, evidence).create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0001"
        )
        assert run["status"] == "open"
        assert run["target_size"] == 3

        rows = (
            await session.execute(
                text(
                    "SELECT ordinal, rule_key, evidence_snapshot FROM calibration_item"
                    " WHERE tenant_id=:tenant_id ORDER BY ordinal"
                ),
                {"tenant_id": evidence["tenant_id"]},
            )
        ).all()
        assert [row.ordinal for row in rows] == [1, 2, 3]
        assert sorted(row.rule_key for row in rows) == [
            "description.length", "h1.missing", "title.length",
        ]
        # Each item must carry its own page's evidence, not the first page's.
        urls = {row.evidence_snapshot["page"]["url"] for row in rows}
        assert len(urls) == 3
        for row in rows:
            snapshot = row.evidence_snapshot
            assert snapshot["observation"]["http_status"] == 200
            assert snapshot["observation"]["final_url"] == snapshot["page"]["url"]
            assert snapshot["finding"]["rule_key"] == row.rule_key


async def test_a_run_is_refused_while_the_crawl_is_unfinished(
    app_engine, stale_evidence
) -> None:
    """Labelling against evidence that is still moving would freeze nothing."""
    async with scoped(app_engine, stale_evidence) as session:
        with pytest.raises(HTTPException) as error:
            await calibration(session, stale_evidence).create_run(
                stale_evidence["site_id"], target_size=20, idempotency_key="calibrate-0002"
            )
        assert error.value.detail == "calibration_evidence_not_ready"


async def test_a_second_open_run_is_refused(app_engine, evidence) -> None:
    async with scoped(app_engine, evidence) as session:
        await calibration(session, evidence).create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0003"
        )
        with pytest.raises(HTTPException) as error:
            await calibration(session, evidence).create_run(
                evidence["site_id"], target_size=20, idempotency_key="calibrate-0004"
            )
        assert error.value.detail == "calibration_run_already_open"


async def test_the_same_key_returns_the_same_run(app_engine, evidence) -> None:
    async with scoped(app_engine, evidence) as session:
        first = await calibration(session, evidence).create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0005"
        )
        second = await calibration(session, evidence).create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0005"
        )
        assert first["id"] == second["id"]
        assert (
            await session.execute(
                text("SELECT count(*) FROM calibration_run WHERE tenant_id=:tenant_id"),
                {"tenant_id": evidence["tenant_id"]},
            )
        ).scalar_one() == 1


async def test_the_same_key_with_a_different_request_is_refused(
    app_engine, evidence
) -> None:
    """Reusing a key for different terms must not silently return the old run."""
    async with scoped(app_engine, evidence) as session:
        await calibration(session, evidence).create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0006"
        )
        with pytest.raises(HTTPException) as error:
            await calibration(session, evidence).create_run(
                evidence["site_id"], target_size=5, idempotency_key="calibrate-0006"
            )
        assert error.value.detail == "idempotency_key_reused"


async def test_two_concurrent_runs_under_one_key_create_one_run(
    app_engine, evidence
) -> None:
    """The idempotency guard added with the others, now actually exercised."""
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    async def attempt():
        async with factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(evidence["tenant_id"])},
            )
            run = await calibration(session, evidence).create_run(
                evidence["site_id"], target_size=20, idempotency_key="calibrate-race1"
            )
            return run["id"]

    try:
        first, second = await asyncio.gather(attempt(), attempt())
        assert first == second

        async with factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(evidence["tenant_id"])},
            )
            assert (
                await session.execute(
                    text("SELECT count(*) FROM calibration_run WHERE tenant_id=:tenant_id"),
                    {"tenant_id": evidence["tenant_id"]},
                )
            ).scalar_one() == 1
    finally:
        # These sessions commit, so the fixture's rollback cannot undo them.
        async with factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(evidence["tenant_id"])},
            )
            for table in ("calibration_item", "calibration_run"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"),
                    {"tenant_id": evidence["tenant_id"]},
                )


async def test_a_review_is_recorded_and_a_repeat_returns_the_first(
    app_engine, evidence
) -> None:
    async with scoped(app_engine, evidence) as session:
        service = calibration(session, evidence)
        await service.create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0007"
        )
        item_id = (
            await session.execute(
                text(
                    "SELECT id FROM calibration_item WHERE tenant_id=:tenant_id AND ordinal=1"
                ),
                {"tenant_id": evidence["tenant_id"]},
            )
        ).scalar_one()
        command = CalibrationReviewCreate(
            accuracy_label="true_positive",
            actionability="accept",
            severity_fit="appropriate",
            notes="checked by hand",
        )
        first = await service.review_item(item_id, command, "review-key-0001")
        second = await service.review_item(item_id, command, "review-key-0001")
        assert first.id == second.id
        assert (
            await session.execute(
                text("SELECT count(*) FROM calibration_review WHERE tenant_id=:tenant_id"),
                {"tenant_id": evidence["tenant_id"]},
            )
        ).scalar_one() == 1


async def test_a_review_key_reused_for_a_different_verdict_is_refused(
    app_engine, evidence
) -> None:
    """A reviewer must not be able to overwrite a recorded label by retrying."""
    async with scoped(app_engine, evidence) as session:
        service = calibration(session, evidence)
        await service.create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0008"
        )
        item_id = (
            await session.execute(
                text(
                    "SELECT id FROM calibration_item WHERE tenant_id=:tenant_id AND ordinal=1"
                ),
                {"tenant_id": evidence["tenant_id"]},
            )
        ).scalar_one()

        def verdict(label: str) -> CalibrationReviewCreate:
            return CalibrationReviewCreate(
                accuracy_label=label,
                actionability="accept",
                severity_fit="appropriate",
                notes="",
            )

        await service.review_item(item_id, verdict("true_positive"), "review-key-0002")
        with pytest.raises(HTTPException) as error:
            await service.review_item(item_id, verdict("false_positive"), "review-key-0002")
        assert error.value.detail == "idempotency_key_reused"


async def test_another_tenant_cannot_read_the_calibration_set(
    app_engine, evidence
) -> None:
    stranger = {**evidence, "tenant_id": uuid4()}
    async with scoped(app_engine, evidence) as session:
        await calibration(session, evidence).create_run(
            evidence["site_id"], target_size=20, idempotency_key="calibrate-0009"
        )
    async with scoped(app_engine, stranger) as session:
        assert (
            await session.execute(text("SELECT count(*) FROM calibration_item"))
        ).scalar_one() == 0
