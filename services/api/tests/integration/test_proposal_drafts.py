"""Opportunity to proposal, against PostgreSQL.

This is the step that made the change loop a loop. It reads five tables and
joins an opportunity to its finding through `opportunity_finding` to learn which
rule it came from, so a mocked session could only confirm the shape the test
already assumed. These cases seed real evidence and run on `seo_autopilot_app`.

The refusals matter more than the success. TheCalcHive's front page reached this
service as a ranked opportunity, and the change it implied would have replaced a
hand-written hero headline with the brand name.
"""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.context import Role, TenantContext
from app.services.proposal_drafts import ProposalDraftService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

SCORING_VERSION_ID = UUID("019d0000-0000-7000-8000-000000000090")
PATH_TEMPLATE = "CalcHive/{path}.html"

HERO = "<h1>Every calculator<br>you'll ever <em>need</em></h1>"


def document(subject: str) -> str:
    return (
        f"<!doctype html><html><head><title>{subject} — Free Online Tool | CalcHive</title>"
        f"</head><body><div id=\"home-view\">{HERO}</div>"
        f"<div class=\"calc-view\" id=\"calc-view\"></div></body></html>"
    )


# The repository as the connector would read it back.
REPOSITORY = {
    "CalcHive/emi-calculator.html": document("EMI Calculator"),
    "CalcHive/index.html": document("CalcHive"),
    "CalcHive/bmi-calculator.html": document("BMI Calculator"),
}


async def read_repository(path: str) -> str | None:
    return REPOSITORY.get(path)


async def _seed(session, ids: dict[str, UUID]) -> None:
    """One tenant, three pages: a calculator, the front page, and a missing file."""
    await session.execute(
        text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'draft','active')"),
        {"id": ids["tenant_id"], "slug": f"draft-{ids['tenant_id'].hex[:8]}"},
    )
    await session.execute(
        text(
            "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,"
            "verified_at) VALUES(:id,:tenant_id,'c','https://calc.example','calc.example',"
            "'recommend','active',now())"
        ),
        {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
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
            "request_hash": sha256(b"draft").hexdigest(),
        },
    )

    pages = (
        ("calculator", "/emi-calculator", "EMI Calculator — Free Online Tool | CalcHive",
         "h1.duplicate_across_site", "open"),
        ("front_page", "/", "CalcHive — Free Online Calculators & Financial Tools",
         "h1.duplicate_across_site", "open"),
        ("absent", "/pace-calculator", "Pace Calculator — Free Online Tool | CalcHive",
         "h1.duplicate_across_site", "open"),
        ("thin", "/bmi-calculator", "BMI Calculator — Free Online Tool | CalcHive",
         "content.thin", "open"),
        ("suppressed", "/love-calculator", "Love Calculator — Free Online Tool | CalcHive",
         "h1.duplicate_across_site", "suppressed"),
    )
    for name, path, title, rule_key, status in pages:
        url = f"https://calc.example{path}"
        page_id, observation_id, finding_id, opportunity_id = uuid4(), uuid4(), uuid4(), uuid4()
        ids[f"page_{name}"] = page_id
        ids[f"opportunity_{name}"] = opportunity_id
        await session.execute(
            text(
                "INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash)"
                " VALUES(:id,:tenant_id,:site_id,:url,:url_hash)"
            ),
            {
                "id": page_id, "tenant_id": ids["tenant_id"], "site_id": ids["site_id"],
                "url": url, "url_hash": sha256(url.encode()).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO page_observation(id,tenant_id,page_id,crawl_job_id,http_status,"
                "final_url,title,meta_description,h1_json,word_count,content_hash,rendered)"
                " VALUES(:id,:tenant_id,:page_id,:crawl_id,200,:url,:title,'A description',"
                "CAST(:h1 AS jsonb),300,:hash,false)"
            ),
            {
                "id": observation_id, "tenant_id": ids["tenant_id"], "page_id": page_id,
                "crawl_id": ids["crawl_id"], "url": url, "title": title,
                "h1": '["Every calculator you\'ll ever need"]',
                "hash": sha256(url.encode()).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO finding(id,tenant_id,site_id,page_id,analysis_run_id,"
                "scoring_version_id,rule_key,severity,evidence_refs,summary,confidence,"
                "fingerprint) VALUES(:id,:tenant_id,:site_id,:page_id,:analysis_id,"
                ":scoring_version_id,:rule_key,'medium','{}'::jsonb,'a finding',0.9,:fingerprint)"
            ),
            {
                "id": finding_id, "tenant_id": ids["tenant_id"], "site_id": ids["site_id"],
                "page_id": page_id, "analysis_id": ids["analysis_id"],
                "scoring_version_id": SCORING_VERSION_ID, "rule_key": rule_key,
                "fingerprint": sha256(finding_id.bytes).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,status,impact,"
                "confidence,urgency,effort,risk,score,scoring_version_id,evidence_refs,"
                "fingerprint) VALUES(:id,:tenant_id,:site_id,:page_id,:title,:status,0.8,0.9,"
                "0.7,0.3,'low',90,:scoring_version_id,'{}'::jsonb,:fingerprint)"
            ),
            {
                "id": opportunity_id, "tenant_id": ids["tenant_id"], "site_id": ids["site_id"],
                "page_id": page_id, "title": f"Fix {rule_key}", "status": status,
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
                "tenant_id": ids["tenant_id"], "opportunity_id": opportunity_id,
                "finding_id": finding_id,
            },
        )


async def _teardown(session, tenant_id: UUID) -> None:
    for table in (
        "opportunity_finding", "opportunity", "finding", "page_score", "analysis_run",
        "page_observation", "page", "crawl_job", "outbox_event", "audit_event", "site",
    ):
        await session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
        )
    await session.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})


@pytest_asyncio.fixture
async def evidence(engine):
    ids: dict[str, UUID] = {
        name: uuid4() for name in ("tenant_id", "site_id", "crawl_id", "analysis_id", "actor_id")
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await _seed(session, ids)
    yield ids
    async with factory() as session, session.begin():
        await _teardown(session, ids["tenant_id"])


def drafts(session, ids: dict[str, UUID], tenant_id: UUID | None = None) -> ProposalDraftService:
    return ProposalDraftService(
        session,
        TenantContext(
            tenant_id=tenant_id or ids["tenant_id"],
            actor_id=ids["actor_id"],
            role=Role.OWNER,
            trace_id="integration",
        ),
        PATH_TEMPLATE,
    )


def scoped(app_engine, tenant_id: UUID):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    class _Scoped:
        async def __aenter__(self):
            self._session = factory()
            await self._session.__aenter__()
            self._transaction = self._session.begin()
            await self._transaction.__aenter__()
            await self._session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            return self._session

        async def __aexit__(self, *exc):
            await self._session.rollback()
            await self._session.__aexit__(None, None, None)

    return _Scoped()


async def test_an_opportunity_becomes_a_change_against_the_right_file(
    app_engine, evidence
) -> None:
    async with scoped(app_engine, evidence["tenant_id"]) as session:
        site_id, command = await drafts(session, evidence).draft_from_opportunity(
            evidence["opportunity_calculator"], read_repository
        )

    assert site_id == evidence["site_id"]
    assert command.target_type == "github_file"
    # The URL is extensionless; the file is not. The template bridges them.
    assert command.target_path == "CalcHive/emi-calculator.html"
    assert command.opportunity_id == evidence["opportunity_calculator"]
    assert command.page_id == evidence["page_calculator"]

    assert "<h1>EMI Calculator</h1>" in command.after_content
    assert HERO not in command.after_content
    assert command.before_content == REPOSITORY["CalcHive/emi-calculator.html"]
    # Exactly one element differs.
    assert command.after_content.replace("<h1>EMI Calculator</h1>", HERO) == command.before_content


async def test_the_rationale_names_the_rule_and_both_headings(app_engine, evidence) -> None:
    async with scoped(app_engine, evidence["tenant_id"]) as session:
        _, command = await drafts(session, evidence).draft_from_opportunity(
            evidence["opportunity_calculator"], read_repository
        )

    assert "h1.duplicate_across_site" in command.rationale
    assert "Every calculator you'll ever need" in command.rationale
    assert "EMI Calculator" in command.rationale


async def test_the_front_page_is_refused_before_any_file_is_read(app_engine, evidence) -> None:
    """The case that would have rewritten a hand-written hero headline."""
    reads: list[str] = []

    async def recording_read(path: str) -> str | None:
        reads.append(path)
        return REPOSITORY.get(path)

    async with scoped(app_engine, evidence["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await drafts(session, evidence).draft_from_opportunity(
                evidence["opportunity_front_page"], recording_read
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "h1_repair_refuses_site_root"
    assert reads == []


async def test_a_rule_with_no_deterministic_repair_is_refused(app_engine, evidence) -> None:
    async with scoped(app_engine, evidence["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await drafts(session, evidence).draft_from_opportunity(
                evidence["opportunity_thin"], read_repository
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "opportunity_has_no_deterministic_repair"


async def test_a_suppressed_opportunity_is_refused(app_engine, evidence) -> None:
    """Suppression usually means the evidence was judged invalid."""
    async with scoped(app_engine, evidence["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await drafts(session, evidence).draft_from_opportunity(
                evidence["opportunity_suppressed"], read_repository
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "opportunity_not_open:suppressed"


async def test_a_target_missing_from_the_repository_is_refused(app_engine, evidence) -> None:
    async with scoped(app_engine, evidence["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await drafts(session, evidence).draft_from_opportunity(
                evidence["opportunity_absent"], read_repository
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "draft_target_not_found:CalcHive/pace-calculator.html"


async def test_an_opportunity_belonging_to_another_tenant_is_not_found(
    app_engine, evidence
) -> None:
    stranger = uuid4()
    async with scoped(app_engine, stranger) as session:
        with pytest.raises(HTTPException) as raised:
            await drafts(session, evidence, tenant_id=stranger).draft_from_opportunity(
                evidence["opportunity_calculator"], read_repository
            )

    assert raised.value.status_code == 404
    assert raised.value.detail == "opportunity_not_found"


async def test_the_same_opportunity_drafts_the_same_change_twice(app_engine, evidence) -> None:
    async with scoped(app_engine, evidence["tenant_id"]) as session:
        first = await drafts(session, evidence).draft_from_opportunity(
            evidence["opportunity_calculator"], read_repository
        )
        second = await drafts(session, evidence).draft_from_opportunity(
            evidence["opportunity_calculator"], read_repository
        )

    assert first == second
