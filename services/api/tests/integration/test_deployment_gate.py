"""The deployment gate, proved against PostgreSQL rather than a mock session.

Deployment is the only path in the system that changes a customer's live site.
Everything guarding it -- the site mode ceiling, the emergency freeze, the
scheduled freeze window, the daily change budget, and idempotency -- is checked
in `ProposalService.deploy_proposal`, and until now every one of those checks
was tested against an `AsyncMock` session that returned whatever the test told
it to. A mock cannot fail a unique constraint, cannot count rows it never
stored, and answers a query the same way whether or not the code asked the
right question. That is the shape of blind spot that left row-level security
inert and let one approver satisfy a two-approver rule.

These cases seed a real proposal chain, run the real service against the real
`seo_autopilot_app` role, and assert on what the database actually holds.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.schemas import DeploymentCreate
from app.core.context import Role, TenantContext
from app.domain.deployments import MockDeploymentAdapter
from app.services.proposals import ProposalService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

BEFORE_CONTENT = "<meta name='description' content='before'>"
AFTER_CONTENT = "<meta name='description' content='after'>"
BASE_HASH = hashlib.sha256(BEFORE_CONTENT.encode()).hexdigest()
PROPOSAL_HASH = hashlib.sha256(AFTER_CONTENT.encode()).hexdigest()

# Seeded by migration 0005.
SCORING_VERSION_ID = UUID("019d0000-0000-7000-8000-000000000090")


class Chain:
    """Ids of one seeded tenant -> site -> page -> opportunity -> proposal."""

    def __init__(self, **ids: UUID) -> None:
        self.__dict__.update(ids)

    tenant_id: UUID
    site_id: UUID
    page_id: UUID
    proposal_id: UUID
    author_id: UUID
    approver_id: UUID


@pytest_asyncio.fixture
async def chain(engine):
    """Commit a complete, approved, deployable proposal, then remove it.

    Committed rather than held in a transaction because the service runs on a
    separate application-role connection and cannot see uncommitted rows.
    """
    ids = {
        name: uuid4()
        for name in (
            "tenant_id", "site_id", "page_id", "crawl_id", "analysis_id",
            "opportunity_id", "proposal_id", "author_id", "approver_id",
        )
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenant(id,slug,name,status)"
                " VALUES(:id,:slug,'deploy gate','active')"
            ),
            {"id": ids["tenant_id"], "slug": f"deploy-{ids['tenant_id'].hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,"
                "mode,status,verified_at,daily_change_budget)"
                " VALUES(:id,:tenant_id,'gate','https://gate.example','gate.example',"
                "'recommend','active',now(),5)"
            ),
            {"id": ids["site_id"], "tenant_id": ids["tenant_id"]},
        )
        await session.execute(
            text(
                "INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash)"
                " VALUES(:id,:tenant_id,:site_id,'https://gate.example/',:url_hash)"
            ),
            {
                "id": ids["page_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "url_hash": hashlib.sha256(b"https://gate.example/").hexdigest(),
            },
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
                "actor": ids["author_id"],
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
                "request_hash": hashlib.sha256(b"request").hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,impact,confidence,"
                "urgency,effort,risk,score,scoring_version_id,evidence_refs,fingerprint)"
                " VALUES(:id,:tenant_id,:site_id,:page_id,'meta description',0.5,0.5,0.5,0.5,"
                "'low',50,:scoring_version_id,'{}'::jsonb,:fingerprint)"
            ),
            {
                "id": ids["opportunity_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "page_id": ids["page_id"],
                "scoring_version_id": SCORING_VERSION_ID,
                "fingerprint": hashlib.sha256(ids["opportunity_id"].bytes).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO proposal(id,tenant_id,site_id,opportunity_id,page_id,author_id,"
                "title,rationale,target_type,target_path,before_content,after_content,"
                "diff_unified,base_hash,proposal_hash,risk,status,expires_at,version)"
                " VALUES(:id,:tenant_id,:site_id,:opportunity_id,:page_id,:author_id,"
                "'Rewrite the meta description','because it is missing','html_meta','/',"
                ":before,:after,'--- a\\n+++ b\\n',:base_hash,:proposal_hash,'low','approved',"
                ":expires_at,1)"
            ),
            {
                "id": ids["proposal_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "opportunity_id": ids["opportunity_id"],
                "page_id": ids["page_id"],
                "author_id": ids["author_id"],
                "before": BEFORE_CONTENT,
                "after": AFTER_CONTENT,
                "base_hash": BASE_HASH,
                "proposal_hash": PROPOSAL_HASH,
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        await session.execute(
            text(
                "INSERT INTO proposal_approval(tenant_id,proposal_id,proposal_version,"
                "approver_id,decision)"
                " VALUES(:tenant_id,:proposal_id,1,:approver_id,'approved')"
            ),
            {
                "tenant_id": ids["tenant_id"],
                "proposal_id": ids["proposal_id"],
                "approver_id": ids["approver_id"],
            },
        )
    yield Chain(**ids)
    async with factory() as session, session.begin():
        for table in (
            "deployment_receipt", "outbox_event", "audit_event", "proposal_approval",
            "proposal", "opportunity", "analysis_run", "crawl_job", "page", "site",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"),
                {"tenant_id": ids["tenant_id"]},
            )
        await session.execute(
            text("DELETE FROM tenant WHERE id=:id"), {"id": ids["tenant_id"]}
        )


def service(session, chain: Chain, role: Role = Role.OWNER) -> ProposalService:
    return ProposalService(
        session,
        TenantContext(
            tenant_id=chain.tenant_id,
            actor_id=chain.approver_id,
            role=role,
            trace_id="integration",
        ),
        deployments_enabled=True,
    )


async def deploy(session, chain: Chain, key: str = "deploy-key-0001"):
    return await service(session, chain).deploy_proposal(
        chain.proposal_id, DeploymentCreate(connector_type="mock"), key, MockDeploymentAdapter("mock")
    )


async def set_site(session, chain: Chain, assignment: str, **params) -> None:
    await session.execute(
        text(f"UPDATE site SET {assignment} WHERE id=:site_id"),
        {"site_id": chain.site_id, **params},
    )


async def test_an_approved_proposal_deploys_and_leaves_a_receipt(
    tenant_session_factory, chain
) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        receipt = await deploy(session, chain)
        assert receipt.status == "applied"
        assert receipt.connector_type == "mock"

        # The receipt, the audit event and the outbox event must all be in the
        # database, not merely returned. A mock session accepts session.add()
        # for a row that a real constraint would reject.
        stored = (
            await session.execute(
                text(
                    "SELECT status FROM deployment_receipt"
                    " WHERE proposal_id=:proposal_id AND tenant_id=:tenant_id"
                ),
                {"proposal_id": chain.proposal_id, "tenant_id": chain.tenant_id},
            )
        ).scalar_one()
        assert stored == "applied"
        assert (
            await session.execute(
                text(
                    "SELECT status FROM proposal WHERE id=:id"),
                {"id": chain.proposal_id},
            )
        ).scalar_one() == "deployed"
        assert (
            await session.execute(
                text(
                    "SELECT count(*) FROM outbox_event"
                    " WHERE aggregate_id=:id AND event_type='proposal.deployed.v1'"
                ),
                {"id": chain.proposal_id},
            )
        ).scalar_one() == 1


async def test_observe_mode_blocks_deployment(tenant_session_factory, chain) -> None:
    """The mode ceiling is the tenant's own choice of how much autonomy to grant."""
    async with tenant_session_factory(chain.tenant_id) as session:
        await set_site(session, chain, "mode='observe'")
        with pytest.raises(HTTPException) as error:
            await deploy(session, chain)
        assert error.value.detail == "site_mode_blocks_deployment"


async def test_emergency_freeze_blocks_deployment(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await set_site(session, chain, "emergency_freeze=true")
        with pytest.raises(HTTPException) as error:
            await deploy(session, chain)
        assert error.value.detail == "emergency_freeze_active"


async def test_a_scheduled_freeze_window_blocks_deployment(
    tenant_session_factory, chain
) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await set_site(
            session,
            chain,
            "freeze_window_start=:start, freeze_window_end=:end",
            start=datetime.now(UTC) - timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(hours=1),
        )
        with pytest.raises(HTTPException) as error:
            await deploy(session, chain)
        assert error.value.detail == "scheduled_freeze_window_active"


async def test_a_freeze_window_that_has_passed_does_not_block(
    tenant_session_factory, chain
) -> None:
    """The window must bound deployment, not end it permanently."""
    async with tenant_session_factory(chain.tenant_id) as session:
        await set_site(
            session,
            chain,
            "freeze_window_start=:start, freeze_window_end=:end",
            start=datetime.now(UTC) - timedelta(hours=2),
            end=datetime.now(UTC) - timedelta(hours=1),
        )
        assert (await deploy(session, chain)).status == "applied"


async def test_the_daily_budget_counts_real_receipts_and_stops_at_it(
    tenant_session_factory, chain
) -> None:
    """The budget is a count of rows, which is exactly what a mock cannot do.

    A mocked session returns whatever count the test hands it, so the query
    could scope by the wrong tenant, the wrong site or the wrong day and the
    test would still pass.
    """
    async with tenant_session_factory(chain.tenant_id) as session:
        await set_site(session, chain, "daily_change_budget=1")
        await session.execute(
            text(
                "INSERT INTO deployment_receipt(tenant_id,site_id,proposal_id,connector_type,"
                "idempotency_key,external_ref,manifest_json,status,deployed_at)"
                " VALUES(:tenant_id,:site_id,:proposal_id,'mock','budget-filler-01',"
                "'mock://filler','{}'::jsonb,'applied',now())"
            ),
            {
                "tenant_id": chain.tenant_id,
                "site_id": chain.site_id,
                "proposal_id": chain.proposal_id,
            },
        )
        with pytest.raises(HTTPException) as error:
            await deploy(session, chain)
        assert error.value.detail == "daily_change_budget_exhausted"


async def test_yesterdays_deployments_do_not_consume_todays_budget(
    tenant_session_factory, chain
) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await set_site(session, chain, "daily_change_budget=1")
        await session.execute(
            text(
                "INSERT INTO deployment_receipt(tenant_id,site_id,proposal_id,connector_type,"
                "idempotency_key,external_ref,manifest_json,status,deployed_at)"
                " VALUES(:tenant_id,:site_id,:proposal_id,'mock','budget-filler-02',"
                "'mock://filler','{}'::jsonb,'applied',now()-interval '2 days')"
            ),
            {
                "tenant_id": chain.tenant_id,
                "site_id": chain.site_id,
                "proposal_id": chain.proposal_id,
            },
        )
        assert (await deploy(session, chain)).status == "applied"


async def test_redeploying_with_the_same_key_returns_the_first_receipt(
    tenant_session_factory, chain
) -> None:
    """Idempotency must hold against the stored row, not an in-memory guess.

    The unique constraint on (tenant_id, idempotency_key) means a second write
    would raise; returning the existing receipt is the behaviour that keeps a
    retried request from being either an error or a second change to the site.
    """
    async with tenant_session_factory(chain.tenant_id) as session:
        first = await deploy(session, chain, key="repeat-key-0001")
        second = await deploy(session, chain, key="repeat-key-0001")
        assert first.id == second.id
        assert (
            await session.execute(
                text(
                    "SELECT count(*) FROM deployment_receipt"
                    " WHERE tenant_id=:tenant_id AND idempotency_key='repeat-key-0001'"
                ),
                {"tenant_id": chain.tenant_id},
            )
        ).scalar_one() == 1


async def test_an_unapproved_proposal_cannot_deploy(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await session.execute(
            text("UPDATE proposal SET status='draft' WHERE id=:id"), {"id": chain.proposal_id}
        )
        with pytest.raises(HTTPException) as error:
            await deploy(session, chain)
        assert error.value.detail == "proposal_must_be_approved_before_deployment"


async def test_an_expired_proposal_is_marked_expired_rather_than_deployed(
    tenant_session_factory, chain
) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await session.execute(
            text("UPDATE proposal SET expires_at=now()-interval '1 hour' WHERE id=:id"),
            {"id": chain.proposal_id},
        )
        with pytest.raises(HTTPException) as error:
            await deploy(session, chain)
        assert error.value.detail == "proposal_expired"
        assert (
            await session.execute(
                text("SELECT status FROM proposal WHERE id=:id"), {"id": chain.proposal_id}
            )
        ).scalar_one() == "expired"


async def test_a_viewer_cannot_deploy(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        with pytest.raises(HTTPException) as error:
            await service(session, chain, role=Role.VIEWER).deploy_proposal(
                chain.proposal_id,
                DeploymentCreate(connector_type="mock"),
                "viewer-key-0001",
                MockDeploymentAdapter("mock"),
            )
        assert error.value.detail == "insufficient_permissions_to_deploy_proposal"


async def test_deployments_disabled_blocks_every_role(tenant_session_factory, chain) -> None:
    """The global switch has to win over an otherwise perfect request."""
    async with tenant_session_factory(chain.tenant_id) as session:
        disabled = ProposalService(
            session,
            TenantContext(
                tenant_id=chain.tenant_id,
                actor_id=chain.approver_id,
                role=Role.OWNER,
                trace_id="integration",
            ),
            deployments_enabled=False,
        )
        with pytest.raises(HTTPException) as error:
            await disabled.deploy_proposal(
                chain.proposal_id,
                DeploymentCreate(connector_type="mock"),
                "disabled-key-01",
                MockDeploymentAdapter("mock"),
            )
        assert error.value.detail == "deployments_disabled"


async def test_row_level_security_backstops_a_mismatched_tenant_context(
    tenant_session_factory, chain
) -> None:
    """The service believes it is the owning tenant; the connection is not.

    Every service query filters on `context.tenant_id`, so a context that has
    drifted from the connection's scope would pass the WHERE clause. Row-level
    security is the layer that must still refuse, and this is the only way to
    observe it -- a mock session has no scope to disagree with.
    """
    stranger = uuid4()
    async with tenant_session_factory(stranger) as session:
        with pytest.raises(HTTPException) as error:
            await service(session, chain).deploy_proposal(
                chain.proposal_id,
                DeploymentCreate(connector_type="mock"),
                "stranger-key-01",
                MockDeploymentAdapter("mock"),
            )
        assert error.value.detail == "proposal_not_found"
