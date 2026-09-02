from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.schemas import DeploymentCreate, ProposalApprovalCreate, ProposalCreate
from app.core.context import Role, TenantContext
from app.db.models import Opportunity, Page, Proposal, Site
from app.domain.deployments import MockDeploymentAdapter
from app.services.proposals import ProposalService


def make_context(role: Role = Role.SEO_MANAGER, actor_id: UUID | None = None) -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        actor_id=actor_id or uuid4(),
        role=role,
        trace_id="tr-prop-test",
    )


@pytest.mark.asyncio
async def test_create_proposal_generates_diff_and_evaluates_policy() -> None:
    context = make_context()
    site_id = uuid4()
    opp_id = uuid4()
    page_id = uuid4()

    mock_site = Site(id=site_id, tenant_id=context.tenant_id, mode="recommend")
    mock_opp = Opportunity(id=opp_id, site_id=site_id, tenant_id=context.tenant_id, evidence_refs={"obs_id": str(uuid4())})
    mock_page = Page(id=page_id, site_id=site_id, tenant_id=context.tenant_id, normalized_url="https://example.com/page")

    session = AsyncMock()
    session.scalar.side_effect = [mock_site, mock_opp, mock_page]
    session.add = MagicMock()

    service = ProposalService(session, context)
    command = ProposalCreate(
        opportunity_id=opp_id,
        page_id=page_id,
        title="Update Meta Description",
        rationale="Improve click-through rate",
        target_type="html_meta",
        target_path="/page",
        before_content="<meta name='description' content='Old text' />",
        after_content="<meta name='description' content='New and improved description text.' />",
    )

    proposal = await service.create_proposal(site_id, command)

    assert proposal.title == "Update Meta Description"
    assert proposal.risk == "low"
    assert proposal.status == "validated"
    assert "--- a//page" in proposal.diff_unified
    assert "+++ b//page" in proposal.diff_unified
    assert session.add.call_count == 3  # Proposal, AuditEvent, OutboxEvent
    assert session.flush.called


@pytest.mark.asyncio
async def test_author_cannot_approve_own_proposal_separation_of_duties() -> None:
    author_id = uuid4()
    context = make_context(role=Role.ADMIN, actor_id=author_id)
    proposal_id = uuid4()

    mock_proposal = Proposal(
        id=proposal_id,
        tenant_id=context.tenant_id,
        site_id=uuid4(),
        opportunity_id=uuid4(),
        page_id=uuid4(),
        author_id=author_id,  # Same as context actor_id
        title="Change title",
        rationale="Testing separation of duties",
        target_type="html_meta",
        target_path="/page",
        before_content="Old",
        after_content="New",
        diff_unified="diff",
        base_hash="a" * 64,
        proposal_hash="b" * 64,
        risk="low",
        status="validated",
        policy_evaluation_json={"required_approver_count": 1},
        expires_at=datetime_future(),
        version=1,
    )

    session = AsyncMock()
    service = ProposalService(session, context)
    service.get_proposal = AsyncMock(return_value=mock_proposal)  # type: ignore[method-assign]

    command = ProposalApprovalCreate(decision="approved", notes="Approving own work")
    with pytest.raises(HTTPException) as exc:
        await service.approve_proposal(proposal_id, command)

    assert exc.value.status_code == 403
    assert exc.value.detail == "author_cannot_approve_own_proposal"


@pytest.mark.asyncio
async def test_deploy_proposal_detects_drift_and_blocks() -> None:
    context = make_context(role=Role.DEVELOPER)
    proposal_id = uuid4()
    base_hash = "a" * 64

    mock_proposal = Proposal(
        id=proposal_id,
        tenant_id=context.tenant_id,
        site_id=uuid4(),
        opportunity_id=uuid4(),
        page_id=uuid4(),
        author_id=uuid4(),
        title="Deployable Title Change",
        rationale="Approved",
        target_type="html_meta",
        target_path="/page",
        before_content="Old Base Content",
        after_content="New Approved Content",
        diff_unified="diff",
        base_hash=base_hash,
        proposal_hash="b" * 64,
        risk="low",
        status="approved",
        policy_evaluation_json={"required_approver_count": 1},
        expires_at=datetime_future(),
        version=1,
    )

    mock_site = Site(
        id=mock_proposal.site_id,
        tenant_id=context.tenant_id,
        mode="recommend",
        emergency_freeze=False,
        daily_change_budget=5,
    )
    session = AsyncMock()
    session.scalar.side_effect = [None, mock_site, 0]
    service = ProposalService(session, context, deployments_enabled=True)
    service.get_proposal = AsyncMock(return_value=mock_proposal)  # type: ignore[method-assign]

    adapter = MockDeploymentAdapter(connector_type="github", enforce_drift=True)
    # Drift: live content does not match base content hash
    command = DeploymentCreate(connector_type="github", current_live_content="Modified out of band live content")

    with pytest.raises(HTTPException) as exc:
        await service.deploy_proposal(proposal_id, command, "idemp-key-12345", adapter)

    assert exc.value.status_code == 409
    assert "Drift detected" in exc.value.detail


@pytest.mark.asyncio
async def test_deploy_proposal_happy_path_with_manifest_and_receipt() -> None:
    context = make_context(role=Role.DEVELOPER)
    proposal_id = uuid4()
    before = "Old Base Content"
    from app.domain.proposals import compute_content_hash

    base_hash = compute_content_hash(before)

    mock_proposal = Proposal(
        id=proposal_id,
        tenant_id=context.tenant_id,
        site_id=uuid4(),
        opportunity_id=uuid4(),
        page_id=uuid4(),
        author_id=uuid4(),
        title="Deployable Title Change",
        rationale="Approved",
        target_type="html_meta",
        target_path="/page",
        before_content=before,
        after_content="New Approved Content",
        diff_unified="diff",
        base_hash=base_hash,
        proposal_hash="b" * 64,
        risk="low",
        status="approved",
        policy_evaluation_json={"required_approver_count": 1},
        expires_at=datetime_future(),
        version=1,
    )

    mock_site = Site(
        id=mock_proposal.site_id,
        tenant_id=context.tenant_id,
        mode="recommend",
        emergency_freeze=False,
        daily_change_budget=5,
    )
    session = AsyncMock()
    session.scalar.side_effect = [None, mock_site, 0]
    session.scalars.return_value = [uuid4()]  # 1 approver
    session.add = MagicMock()

    service = ProposalService(session, context, deployments_enabled=True)
    service.get_proposal = AsyncMock(return_value=mock_proposal)  # type: ignore[method-assign]

    adapter = MockDeploymentAdapter(connector_type="github", enforce_drift=True)
    command = DeploymentCreate(connector_type="github", current_live_content=before)

    receipt = await service.deploy_proposal(proposal_id, command, "idemp-key-12345", adapter)

    assert receipt.status == "applied"
    assert "https://github.com" in receipt.external_ref
    assert receipt.manifest_json["proposal_id"] == str(proposal_id)
    assert mock_proposal.status == "deployed"
    assert session.add.call_count == 3  # Receipt, AuditEvent, OutboxEvent


@pytest.mark.asyncio
async def test_deployment_defaults_closed_before_adapter_execution() -> None:
    context = make_context(role=Role.DEVELOPER)
    service = ProposalService(AsyncMock(), context)
    adapter = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await service.deploy_proposal(
            uuid4(),
            DeploymentCreate(connector_type="mock", current_live_content="base"),
            "idemp-key-closed",
            adapter,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "deployments_disabled"
    adapter.deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_viewer_cannot_deploy_even_when_feature_flag_is_enabled() -> None:
    context = make_context(role=Role.VIEWER)
    service = ProposalService(AsyncMock(), context, deployments_enabled=True)

    with pytest.raises(HTTPException) as exc:
        await service.deploy_proposal(
            uuid4(),
            DeploymentCreate(connector_type="mock", current_live_content="base"),
            "idemp-key-viewer",
            AsyncMock(),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "insufficient_permissions_to_deploy_proposal"


@pytest.mark.asyncio
async def test_emergency_freeze_blocks_deployment() -> None:
    context = make_context(role=Role.DEVELOPER)
    proposal = Proposal(
        id=uuid4(), tenant_id=context.tenant_id, site_id=uuid4(), opportunity_id=uuid4(),
        page_id=uuid4(), author_id=uuid4(), title="Frozen", rationale="Frozen",
        target_type="html_meta", target_path="/page", before_content="old",
        after_content="new", diff_unified="diff", base_hash="a" * 64,
        proposal_hash="b" * 64, risk="low", status="approved",
        policy_evaluation_json={"required_approver_count": 1}, expires_at=datetime_future(), version=1,
    )
    site = Site(
        id=proposal.site_id, tenant_id=context.tenant_id, mode="recommend",
        emergency_freeze=True, daily_change_budget=5,
    )
    session = AsyncMock()
    session.scalar.side_effect = [None, site]
    service = ProposalService(session, context, deployments_enabled=True)
    service.get_proposal = AsyncMock(return_value=proposal)  # type: ignore[method-assign]
    adapter = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await service.deploy_proposal(
            proposal.id,
            DeploymentCreate(connector_type="mock", current_live_content="old"),
            "idemp-key-freeze",
            adapter,
        )

    assert exc.value.detail == "emergency_freeze_active"
    adapter.deploy.assert_not_awaited()


def datetime_future() -> object:
    from datetime import UTC, datetime, timedelta
    return datetime.now(UTC) + timedelta(days=7)
