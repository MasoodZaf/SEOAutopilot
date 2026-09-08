from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.core.context import Role, TenantContext
from app.db.models import (
    DeploymentReceipt,
    PostDeployVerification,
    Proposal,
    SearchMetric,
)
from app.domain.measurement import (
    calculate_measurement_delta,
    changed_lines,
    verify_rendered_content,
)
from app.services.measurements import MeasurementService


def make_context(role: Role = Role.SEO_MANAGER, actor_id: UUID | None = None) -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        actor_id=actor_id or uuid4(),
        role=role,
        trace_id="tr-meas-test",
    )


def test_verify_rendered_content_success_and_failure() -> None:
    expected = "<meta name='description' content='High performance cloud databases' />"
    live_ok = "<html><head><meta name='description' content='High performance cloud databases' /></head><body></body></html>"
    res_ok = verify_rendered_content(expected, live_ok, 200)
    assert res_ok.is_verified
    assert res_ok.http_status == 200
    assert "High performance cloud databases" in res_ok.observed_snippet

    live_missing = "<html><head><meta name='description' content='Old description' /></head></html>"
    res_fail = verify_rendered_content(expected, live_missing, 200)
    assert not res_fail.is_verified

    res_500 = verify_rendered_content(expected, "Internal Server Error", 500)
    assert not res_500.is_verified
    assert res_500.http_status == 500


def test_calculate_measurement_delta_with_sparse_data_detection() -> None:
    baseline = {"clicks": 20.0, "impressions": 80.0, "ctr": 0.25, "position": 14.0}
    followup = {"clicks": 35.0, "impressions": 95.0, "ctr": 0.3684, "position": 9.5}

    delta = calculate_measurement_delta(baseline, followup)
    assert delta.clicks_delta == 15.0
    assert delta.impressions_delta == 15.0
    assert delta.position_delta == 4.5  # 14.0 -> 9.5 is an improvement of +4.5 positions
    assert delta.is_sparse  # < 100 impressions
    assert delta.confidence_score == 0.35
    assert any("empirical association" in c for c in delta.caveats)
    assert any("sparse" in c for c in delta.caveats)


@pytest.mark.asyncio
async def test_verify_deployment_service_flow() -> None:
    context = make_context()
    proposal_id = uuid4()
    site_id = uuid4()
    page_id = uuid4()
    receipt_id = uuid4()

    mock_proposal = Proposal(
        id=proposal_id,
        tenant_id=context.tenant_id,
        site_id=site_id,
        opportunity_id=uuid4(),
        page_id=page_id,
        author_id=uuid4(),
        title="Title change",
        rationale="Improve SEO",
        target_type="html_meta",
        target_path="/page",
        before_content="<title>Old</title>",
        after_content="<title>New Super Title</title>",
        diff_unified="diff",
        base_hash="a" * 64,
        proposal_hash="b" * 64,
        risk="low",
        status="deployed",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    mock_receipt = DeploymentReceipt(
        id=receipt_id,
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
    # First call: get proposal; Second call: get receipt; Third call: get existing verification (None)
    session.scalar.side_effect = [mock_proposal, mock_receipt, None]
    session.add = MagicMock()

    service = MeasurementService(session, context)
    verification = await service.verify_deployment(
        proposal_id=proposal_id,
        live_body="<html><head><title>New Super Title</title></head></html>",
        live_status=200,
    )

    assert verification.status == "verified"
    assert verification.http_status == 200
    assert mock_receipt.verified_at is not None
    assert session.add.call_count == 3  # Verification, AuditEvent, OutboxEvent
    assert session.flush.called


@pytest.mark.asyncio
async def test_verification_rejects_missing_live_evidence() -> None:
    context = make_context()
    proposal = Proposal(
        id=uuid4(), tenant_id=context.tenant_id, site_id=uuid4(), opportunity_id=uuid4(),
        page_id=uuid4(), author_id=uuid4(), title="Title", rationale="Verify",
        target_type="html_meta", target_path="/page", before_content="old",
        after_content="new", diff_unified="diff", base_hash="a" * 64,
        proposal_hash="b" * 64, risk="low", status="deployed",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    receipt = DeploymentReceipt(
        id=uuid4(), tenant_id=context.tenant_id, site_id=proposal.site_id,
        proposal_id=proposal.id, connector_type="mock", idempotency_key="verify-no-live",
        external_ref="mock://receipt", manifest_json={}, status="applied",
        deployed_at=datetime.now(UTC),
    )
    session = AsyncMock()
    session.scalar.side_effect = [proposal, receipt]

    with pytest.raises(HTTPException) as exc:
        await MeasurementService(session, context).verify_deployment(proposal.id)

    assert exc.value.status_code == 409
    assert exc.value.detail == "live_verification_evidence_required"
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_calculate_measurement_series_service_flow() -> None:
    context = make_context()
    proposal_id = uuid4()
    site_id = uuid4()
    page_id = uuid4()
    deployed_at = datetime.now(UTC) - timedelta(days=30)

    mock_proposal = Proposal(
        id=proposal_id,
        tenant_id=context.tenant_id,
        site_id=site_id,
        opportunity_id=uuid4(),
        page_id=page_id,
        author_id=uuid4(),
        title="Title change",
        rationale="Improve SEO",
        target_type="html_meta",
        target_path="/page",
        before_content="Old",
        after_content="New",
        diff_unified="diff",
        base_hash="a" * 64,
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
        deployed_at=deployed_at,
    )

    session = AsyncMock()
    verification = PostDeployVerification(
        id=uuid4(), tenant_id=context.tenant_id, site_id=site_id,
        proposal_id=proposal_id, deployment_receipt_id=mock_receipt.id,
        page_id=page_id, status="verified", expected_pattern="New",
    )
    # 1: proposal, 2: receipt, 3: existing measurement, 4: verification
    session.scalar.side_effect = [mock_proposal, mock_receipt, None, verification]

    # Baseline search metrics
    b_metrics = [
        SearchMetric(
            tenant_id=context.tenant_id,
            site_id=site_id,
            page_id=page_id,
            metric_date=(deployed_at - timedelta(days=10)).date(),
            clicks=100,
            impressions=2000,
            ctr=0.05,
            position=15.0,
        )
    ]
    # Follow-up search metrics
    f_metrics = [
        SearchMetric(
            tenant_id=context.tenant_id,
            site_id=site_id,
            page_id=page_id,
            metric_date=(deployed_at + timedelta(days=10)).date(),
            clicks=180,
            impressions=2500,
            ctr=0.072,
            position=11.2,
        )
    ]
    session.scalars.side_effect = [b_metrics, f_metrics]
    session.add = MagicMock()

    service = MeasurementService(session, context)
    series = await service.calculate_or_get_measurement(proposal_id)

    assert series.proposal_id == proposal_id
    assert series.confidence_score == 0.90
    assert series.delta_metrics["clicks_delta"] == 80.0
    assert series.delta_metrics["position_delta"] == 3.8
    assert session.add.call_count == 3  # MeasurementSeries, AuditEvent, OutboxEvent
    assert session.flush.called


@pytest.mark.asyncio
async def test_measurement_requires_verified_deployment() -> None:
    context = make_context()
    proposal = Proposal(
        id=uuid4(), tenant_id=context.tenant_id, site_id=uuid4(), opportunity_id=uuid4(),
        page_id=uuid4(), author_id=uuid4(), title="Title", rationale="Measure",
        target_type="html_meta", target_path="/page", before_content="old",
        after_content="new", diff_unified="diff", base_hash="a" * 64,
        proposal_hash="b" * 64, risk="low", status="deployed",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    receipt = DeploymentReceipt(
        id=uuid4(), tenant_id=context.tenant_id, site_id=proposal.site_id,
        proposal_id=proposal.id, connector_type="mock", idempotency_key="measure-unverified",
        external_ref="mock://receipt", manifest_json={}, status="applied",
        deployed_at=datetime.now(UTC) - timedelta(days=30),
    )
    session = AsyncMock()
    session.scalar.side_effect = [proposal, receipt, None, None]

    with pytest.raises(HTTPException) as exc:
        await MeasurementService(session, context).calculate_or_get_measurement(proposal.id)

    assert exc.value.status_code == 409
    assert exc.value.detail == "deployment_not_verified"
    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_measurement_requires_complete_followup_window() -> None:
    context = make_context()
    proposal = Proposal(
        id=uuid4(), tenant_id=context.tenant_id, site_id=uuid4(), opportunity_id=uuid4(),
        page_id=uuid4(), author_id=uuid4(), title="Title", rationale="Measure",
        target_type="html_meta", target_path="/page", before_content="old",
        after_content="new", diff_unified="diff", base_hash="a" * 64,
        proposal_hash="b" * 64, risk="low", status="deployed",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    receipt = DeploymentReceipt(
        id=uuid4(), tenant_id=context.tenant_id, site_id=proposal.site_id,
        proposal_id=proposal.id, connector_type="mock", idempotency_key="measure-early",
        external_ref="mock://receipt", manifest_json={}, status="applied",
        deployed_at=datetime.now(UTC) - timedelta(days=3),
    )
    verification = PostDeployVerification(
        id=uuid4(), tenant_id=context.tenant_id, site_id=proposal.site_id,
        proposal_id=proposal.id, deployment_receipt_id=receipt.id,
        page_id=proposal.page_id, status="verified", expected_pattern="new",
    )
    session = AsyncMock()
    session.scalar.side_effect = [proposal, receipt, None, verification]

    with pytest.raises(HTTPException) as exc:
        await MeasurementService(session, context).calculate_or_get_measurement(proposal.id)

    assert exc.value.status_code == 409
    assert exc.value.detail == "measurement_window_incomplete"
    session.scalars.assert_not_awaited()


def test_the_evidence_is_what_the_change_adds() -> None:
    """Not the whole file, which is what `after_content` holds.

    Requiring the entire document verbatim asks whether the served page is
    byte-identical to the source -- a question that answers "no" the moment
    anything else in the file changes, and that never distinguishes this change
    from any other.
    """
    diff = (
        "--- a/WordKit/rhyme-tool.html\n"
        "+++ b/WordKit/rhyme-tool.html\n"
        "@@ -79,7 +79,7 @@\n"
        ' <div class="hero">\n'
        "-    <h1>The word<br>toolkit for<em>every game</em></h1>\n"
        "+    <h1>Rhyme Finder</h1>\n"
        '     <p class="hero-sub">Unscramble letters</p>\n'
    )
    # The `+++` header line is a header, not an addition.
    assert changed_lines(diff) == ["<h1>Rhyme Finder</h1>"]


def test_a_page_is_verified_on_the_changed_line_not_on_its_indentation() -> None:
    live = "<html><body><div class='hero'>\n  <h1>Rhyme Finder</h1>\n</div></body></html>"
    assert verify_rendered_content("<h1>Rhyme Finder</h1>", live, 200).is_verified

    # Every added line has to be there, not merely one of them.
    both = "<h1>Rhyme Finder</h1>\n<title>Rhyme Finder — WordKit</title>"
    result = verify_rendered_content(both, live, 200)
    assert not result.is_verified
    assert "1 of 2" in result.notes
    # It cannot tell an unmerged pull request from a superseded change by
    # looking at the page, so it says so rather than picking one.
    assert "not merged yet or has since been superseded" in result.notes


def test_an_error_page_is_never_verified() -> None:
    live = "<h1>Rhyme Finder</h1>"
    assert not verify_rendered_content(live, live, 404).is_verified
    assert not verify_rendered_content(live, live, 500).is_verified
