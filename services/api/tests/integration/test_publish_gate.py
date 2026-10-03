"""Publishing from the app, against PostgreSQL.

Publish is deploy plus merge, so it must hold every deployment gate and three
of its own: the site's opt-in, the change classes kept for GitHub, and a full
set of approvals for the current version. A refused merge must leave the
receipt for the pull request it may just have opened.
"""

from dataclasses import replace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.core.context import Role, TenantContext
from app.domain.deployments import DeploymentRequest, DeploymentResult, MockDeploymentAdapter
from app.domain.github_adapter import GitHubMergeRefused, MergeResult
from app.services.proposals import ProposalService
from tests.conftest import requires_database
from tests.integration import test_deployment_gate as gate

pytestmark = [pytest.mark.asyncio, requires_database]

# The deployment gate's seeded, approved proposal chain is the starting point.
chain = gate.chain
Chain = gate.Chain
AFTER_CONTENT = gate.AFTER_CONTENT
set_site = gate.set_site


class FakePublisher:
    """Opens a pretend pull request #41 and records what it is asked to merge."""

    connector_type = "github"

    def __init__(self, refuse: str | None = None) -> None:
        self.inner = MockDeploymentAdapter("github")
        self.refuse = refuse
        self.merged: list[tuple[int, str, str]] = []

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        result = await self.inner.deploy(request)
        return replace(result, manifest_json={**result.manifest_json, "pull_request_number": 41})

    async def rollback(self, request):  # pragma: no cover - not exercised here
        return await self.inner.rollback(request)

    async def merge(self, number: int, target_path: str, expected_hash: str) -> MergeResult:
        if self.refuse:
            raise GitHubMergeRefused(self.refuse)
        self.merged.append((number, target_path, expected_hash))
        return MergeResult("merge-sha-1", "https://github.com/o/r/pull/41", already_merged=False)


def publisher(session, chain: Chain, role: Role = Role.OWNER, enabled: bool = True) -> ProposalService:
    return ProposalService(
        session,
        TenantContext(tenant_id=chain.tenant_id, actor_id=chain.approver_id, role=role, trace_id="integration"),
        deployments_enabled=enabled,
    )


async def allow(session, chain: Chain, approvals: int = 1) -> None:
    await set_site(session, chain, "publish_from_app=true")
    await session.execute(
        text("UPDATE proposal SET policy_evaluation_json=jsonb_build_object('required_approver_count', CAST(:n AS int)) WHERE id=:id"),
        {"n": approvals, "id": chain.proposal_id},
    )


async def test_one_click_opens_and_merges_the_reviewed_change(tenant_session_factory, chain) -> None:
    adapter = FakePublisher()
    async with tenant_session_factory(chain.tenant_id) as session:
        await allow(session, chain)
        receipt, refusal = await publisher(session, chain).publish_proposal(chain.proposal_id, "publish-key-0001", adapter)
        assert refusal is None
        assert receipt.manifest_json["pull_request_state"] == "merged"
        assert receipt.manifest_json["published_by"] == str(chain.approver_id)
        [(number, path, expected)] = adapter.merged
        assert number == 41 and path == "/"
        assert expected == __import__("hashlib").sha256(AFTER_CONTENT.encode()).hexdigest()
        events = (await session.execute(text(
            "SELECT event_type FROM outbox_event WHERE aggregate_id=:id ORDER BY event_type"),
            {"id": chain.proposal_id})).scalars().all()
        assert events == ["proposal.deployed.v1", "proposal.published.v1"]

        # A second click is a read: already published, nothing merged again.
        again, _ = await publisher(session, chain).publish_proposal(chain.proposal_id, "publish-key-0001", adapter)
        assert again.id == receipt.id and len(adapter.merged) == 1


async def test_it_is_off_until_the_site_allows_it(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        with pytest.raises(HTTPException) as refused:
            await publisher(session, chain).publish_proposal(chain.proposal_id, "publish-key-0002", FakePublisher())
    assert refused.value.detail == "publishing_from_app_disabled"


async def test_every_approval_for_the_version_is_required(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await allow(session, chain, approvals=2)
        with pytest.raises(HTTPException) as refused:
            await publisher(session, chain).publish_proposal(chain.proposal_id, "publish-key-0003", FakePublisher())
    assert refused.value.detail == "publish_requires_approvals:1_of_2"


async def test_indexing_changes_stay_a_merge_on_github(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await allow(session, chain)
        await session.execute(
            text("UPDATE proposal SET after_content=:after WHERE id=:id"),
            {"after": '<meta name="robots" content="noindex">', "id": chain.proposal_id},
        )
        with pytest.raises(HTTPException) as refused:
            await publisher(session, chain).publish_proposal(chain.proposal_id, "publish-key-0004", FakePublisher())
    assert refused.value.detail == "publish_requires_github_merge:indexing_control_change"


@pytest.mark.parametrize(
    ("assignment", "detail"),
    [("emergency_freeze=true", "emergency_freeze_active"), ("mode='observe'", "site_mode_blocks_deployment")],
)
async def test_the_deployment_gates_still_hold(tenant_session_factory, chain, assignment, detail) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await allow(session, chain)
        await set_site(session, chain, assignment)
        with pytest.raises(HTTPException) as refused:
            await publisher(session, chain).publish_proposal(chain.proposal_id, "publish-key-0005", FakePublisher())
    assert refused.value.detail == detail


async def test_the_global_switch_and_roles_still_hold(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await allow(session, chain)
        with pytest.raises(HTTPException) as switched_off:
            await publisher(session, chain, enabled=False).publish_proposal(chain.proposal_id, "publish-key-0006", FakePublisher())
        # A developer may open pull requests and a SEO manager may approve; neither publishes alone.
        for role in (Role.DEVELOPER, Role.SEO_MANAGER):
            with pytest.raises(HTTPException) as forbidden:
                await publisher(session, chain, role=role).publish_proposal(chain.proposal_id, "publish-key-0007", FakePublisher())
            assert forbidden.value.status_code == 403
    assert switched_off.value.detail == "deployments_disabled"


async def test_a_refused_merge_keeps_the_pull_request_receipt(tenant_session_factory, chain) -> None:
    async with tenant_session_factory(chain.tenant_id) as session:
        await allow(session, chain)
        receipt, refusal = await publisher(session, chain).publish_proposal(
            chain.proposal_id, "publish-key-0008", FakePublisher(refuse="github_refused_merge")
        )
        assert refusal == "github_refused_merge"
        assert receipt.manifest_json["pull_request_number"] == 41
        assert "published_at" not in receipt.manifest_json
        audited = (await session.execute(text(
            "SELECT count(*) FROM audit_event WHERE resource_id=:id AND action='proposal.publish_refused'"),
            {"id": str(chain.proposal_id)})).scalar_one()
        assert audited == 1


async def test_another_tenant_cannot_publish(tenant_session_factory, chain) -> None:
    from uuid import uuid4

    other = uuid4()
    async with tenant_session_factory(other) as session:
        service = ProposalService(
            session, TenantContext(tenant_id=other, actor_id=chain.approver_id, role=Role.OWNER, trace_id="i"),
            deployments_enabled=True,
        )
        with pytest.raises(HTTPException) as refused:
            await service.publish_proposal(chain.proposal_id, "publish-key-0009", FakePublisher())
    assert refused.value.status_code == 404
