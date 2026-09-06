from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.schemas import GovernanceSettingsUpdate
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, DeploymentReceipt, Proposal, Site
from app.domain.deployments import RollbackResult
from app.domain.governance import (
    check_autopilot_execution_eligibility,
    simulate_policy_on_proposals,
)
from app.services.governance import GovernanceService


def make_context(role: Role = Role.ADMIN, actor_id: UUID | None = None) -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        actor_id=actor_id or uuid4(),
        role=role,
        trace_id="tr-gov-test",
    )


def test_autopilot_eligibility_evaluations() -> None:
    now = datetime.now(UTC)

    # 1. Emergency freeze active
    freeze_res = check_autopilot_execution_eligibility(
        site_mode="autopilot",
        autopilot_enabled=True,
        emergency_freeze=True,
        daily_change_budget=5,
        freeze_window_start=None,
        freeze_window_end=None,
        today_deployments_count=0,
        proposal_risk="low",
        proposal_target_type="html_meta",
        now=now,
    )
    assert not freeze_res.is_eligible
    assert freeze_res.reason == "emergency_freeze_active"

    # 2. Daily change budget exhausted
    budget_res = check_autopilot_execution_eligibility(
        site_mode="autopilot",
        autopilot_enabled=True,
        emergency_freeze=False,
        daily_change_budget=5,
        freeze_window_start=None,
        freeze_window_end=None,
        today_deployments_count=5,
        proposal_risk="low",
        proposal_target_type="html_meta",
        now=now,
    )
    assert not budget_res.is_eligible
    assert "daily_change_budget_exhausted" in budget_res.reason

    # 3. Scheduled freeze window
    window_start = now - timedelta(hours=1)
    window_end = now + timedelta(hours=2)
    sched_res = check_autopilot_execution_eligibility(
        site_mode="autopilot",
        autopilot_enabled=True,
        emergency_freeze=False,
        daily_change_budget=5,
        freeze_window_start=window_start,
        freeze_window_end=window_end,
        today_deployments_count=0,
        proposal_risk="low",
        proposal_target_type="html_meta",
        now=now,
    )
    assert not sched_res.is_eligible
    assert sched_res.reason == "scheduled_freeze_window_active"

    # 4. Non-allowlisted target type
    target_res = check_autopilot_execution_eligibility(
        site_mode="autopilot",
        autopilot_enabled=True,
        emergency_freeze=False,
        daily_change_budget=5,
        freeze_window_start=None,
        freeze_window_end=None,
        today_deployments_count=0,
        proposal_risk="low",
        proposal_target_type="content_edit",
        now=now,
    )
    assert not target_res.is_eligible
    assert "not_allowlisted" in target_res.reason

    # 5. Happy path
    happy_res = check_autopilot_execution_eligibility(
        site_mode="autopilot",
        autopilot_enabled=True,
        emergency_freeze=False,
        daily_change_budget=5,
        freeze_window_start=None,
        freeze_window_end=None,
        today_deployments_count=2,
        proposal_risk="low",
        proposal_target_type="html_meta",
        now=now,
    )
    assert happy_res.is_eligible


def test_simulate_policy_on_proposals() -> None:
    now = datetime.now(UTC)

    class MockProp:
        def __init__(self, risk: str, target_type: str, title: str) -> None:
            self.id = uuid4()
            self.risk = risk
            self.target_type = target_type
            self.title = title

    proposals = [
        MockProp("low", "html_meta", "Meta title update"),
        MockProp("medium", "content_edit", "Large copy rewrite"),
        MockProp("prohibited", "html_meta", "Guaranteed ranking claim"),
    ]

    sim = simulate_policy_on_proposals(
        site_mode="autopilot",
        autopilot_enabled=True,
        emergency_freeze=False,
        daily_change_budget=5,
        freeze_window_start=None,
        freeze_window_end=None,
        proposals=proposals,
        now=now,
    )

    assert sim["evaluated_proposals_count"] == 3
    assert sim["auto_deployable_count"] == 1
    assert sim["review_required_count"] == 1
    assert sim["prohibited_count"] == 1


@pytest.mark.asyncio
async def test_emergency_freeze_and_unfreeze_service_flow() -> None:
    context = make_context(role=Role.ADMIN)
    site_id = uuid4()

    mock_site = Site(
        id=site_id,
        tenant_id=context.tenant_id,
        name="Test Site",
        canonical_origin="https://example.com",
        normalized_host="example.com",
        mode="autopilot",
        autopilot_enabled=True,
        emergency_freeze=False,
        daily_change_budget=5,
    )

    session = AsyncMock()
    session.scalar.side_effect = [mock_site, mock_site, 0]  # get site for freeze, get site for status, count
    session.add = MagicMock()

    service = GovernanceService(session, context)

    # Freeze
    status_frozen = await service.trigger_emergency_freeze(site_id, notes="Investigating anomaly")
    assert status_frozen.emergency_freeze
    assert mock_site.emergency_freeze
    assert session.add.call_count == 2  # AuditEvent and OutboxEvent

    # Unfreeze
    session.scalar.side_effect = [mock_site, mock_site, 0]
    status_active = await service.lift_emergency_freeze(site_id)
    assert not status_active.emergency_freeze
    assert not mock_site.emergency_freeze


@pytest.mark.asyncio
async def test_rollback_fails_closed_until_connector_is_configured() -> None:
    context = make_context(role=Role.ADMIN)
    proposal_id = uuid4()
    site_id = uuid4()
    base_hash = "f" * 64

    mock_proposal = Proposal(
        id=proposal_id,
        tenant_id=context.tenant_id,
        site_id=site_id,
        opportunity_id=uuid4(),
        page_id=uuid4(),
        author_id=uuid4(),
        title="Title change",
        rationale="Improve SEO",
        target_type="html_meta",
        target_path="/page",
        before_content="<title>Old</title>",
        after_content="<title>New</title>",
        diff_unified="diff",
        base_hash=base_hash,
        proposal_hash="b" * 64,
        risk="low",
        status="deployed",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    mock_receipt = DeploymentReceipt(
        id=uuid4(),
        tenant_id=context.tenant_id,
        site_id=site_id,
        proposal_id=proposal_id,
        connector_type="github",
        idempotency_key="deploy-12345",
        external_ref="https://github.com/org/repo/pull/1",
        manifest_json={},
        status="applied",
        deployed_at=datetime.now(UTC) - timedelta(days=1),
    )

    session = AsyncMock()
    session.scalar.side_effect = [mock_proposal, mock_receipt]
    session.add = MagicMock()

    service = GovernanceService(session, context)
    with pytest.raises(HTTPException) as exc:
        await service.rollback_deployment(proposal_id, notes="Rollback drill")

    assert exc.value.status_code == 409
    assert exc.value.detail == "rollback_connector_not_configured"
    assert mock_receipt.status == "applied"
    assert mock_proposal.status == "deployed"
    session.flush.assert_not_awaited()


def _deployed_pair(context, site_id, proposal_id):
    """A proposal that really is live, and the receipt that says so."""
    proposal = Proposal(
        id=proposal_id,
        tenant_id=context.tenant_id,
        site_id=site_id,
        opportunity_id=uuid4(),
        page_id=uuid4(),
        author_id=uuid4(),
        title="Title change",
        rationale="Improve SEO",
        target_type="html_meta",
        target_path="/page",
        before_content="<title>Old</title>",
        after_content="<title>New</title>",
        diff_unified="diff",
        base_hash="f" * 64,
        proposal_hash="b" * 64,
        risk="low",
        status="deployed",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    receipt = DeploymentReceipt(
        id=uuid4(),
        tenant_id=context.tenant_id,
        site_id=site_id,
        proposal_id=proposal_id,
        connector_type="github",
        idempotency_key="deploy-12345",
        external_ref="https://github.com/org/repo/pull/1",
        manifest_json={"pull_request_number": 1},
        status="applied",
        deployed_at=datetime.now(UTC) - timedelta(days=1),
    )
    return proposal, receipt


class _Adapter:
    """Returns whichever of the two rollback outcomes the test is about."""

    def __init__(self, status: str, detail: str) -> None:
        self._result = RollbackResult(
            status=status,
            external_ref="https://github.com/org/repo/pull/2",
            restored_hash="a" * 64,
            detail=detail,
        )

    async def rollback(self, request):
        return self._result


async def _rollback(context, proposal, receipt, adapter):
    session = AsyncMock()
    session.scalar.side_effect = [proposal, receipt, None]
    session.add = MagicMock()
    await GovernanceService(session, context).rollback_deployment(
        proposal.id, notes="drill", adapter=adapter
    )
    return session


@pytest.mark.asyncio
async def test_an_unmerged_revert_leaves_the_change_deployed() -> None:
    """The receipt must not claim an undo that is waiting on a person.

    On the pilot site this receipt read `rolled_back` and the proposal read
    `failed`, while the page still served the deployed content and the revert
    pull request sat open, then closed, never merged. Every structured field
    said the change was gone; only a free-text detail string said otherwise.
    """
    context = make_context(role=Role.ADMIN)
    proposal, receipt = _deployed_pair(context, uuid4(), uuid4())

    await _rollback(
        context,
        proposal,
        receipt,
        _Adapter("pending", "revert_pull_request_opened_not_merged"),
    )

    assert receipt.status == "rollback_pending"
    # Still deployed, because it still is.
    assert proposal.status == "deployed"


@pytest.mark.asyncio
async def test_a_reversal_the_adapter_performed_itself_is_a_rollback() -> None:
    """Closing a pull request that never merged really does undo it."""
    context = make_context(role=Role.ADMIN)
    proposal, receipt = _deployed_pair(context, uuid4(), uuid4())

    await _rollback(
        context,
        proposal,
        receipt,
        _Adapter("applied", "pull_request_closed_before_merge"),
    )

    assert receipt.status == "rolled_back"
    assert proposal.status == "failed"


@pytest.mark.asyncio
async def test_the_audit_trail_distinguishes_the_two() -> None:
    """Whoever reconstructs this later reads the log, not the receipt."""
    context = make_context(role=Role.ADMIN)
    proposal, receipt = _deployed_pair(context, uuid4(), uuid4())

    session = await _rollback(
        context,
        proposal,
        receipt,
        _Adapter("pending", "revert_pull_request_opened_not_merged"),
    )

    events = [call.args[0] for call in session.add.call_args_list]
    audit = [event for event in events if isinstance(event, AuditEvent)]
    assert [event.action for event in audit] == ["proposal.rollback_requested"]
    assert audit[0].metadata_json["change_reversed"] is False
    assert audit[0].metadata_json["receipt_status"] == "rollback_pending"


@pytest.mark.asyncio
async def test_viewer_cannot_request_rollback() -> None:
    service = GovernanceService(AsyncMock(), make_context(role=Role.VIEWER))

    with pytest.raises(HTTPException) as exc:
        await service.rollback_deployment(uuid4())

    assert exc.value.status_code == 403
    assert exc.value.detail == "insufficient_permissions_to_rollback_deployment"


@pytest.mark.asyncio
async def test_unauthorized_viewer_cannot_change_governance() -> None:
    context = make_context(role=Role.VIEWER)
    site_id = uuid4()

    session = AsyncMock()
    service = GovernanceService(session, context)

    with pytest.raises(HTTPException) as exc:
        await service.update_governance_settings(site_id, GovernanceSettingsUpdate(autopilot_enabled=True))

    assert exc.value.status_code == 403
