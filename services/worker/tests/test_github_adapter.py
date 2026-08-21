from uuid import uuid4

import pytest
from app.deployments.base import (
    DeploymentManifest,
    DeploymentRequest,
    DriftDetectedError,
)
from app.deployments.github_adapter import GitHubDeploymentAdapter, format_pr_body


@pytest.mark.asyncio
async def test_github_adapter_creates_pr_and_detects_drift() -> None:
    manifest = DeploymentManifest(
        tenant_id=str(uuid4()),
        site_id=str(uuid4()),
        proposal_id=str(uuid4()),
        target_path="src/app/page.tsx",
        base_hash="a" * 64,
        proposal_hash="b" * 64,
        author_id=str(uuid4()),
        approver_ids=[str(uuid4())],
        deployed_at="2026-08-21T12:00:00Z",
    )

    body = format_pr_body(manifest, "--- a\n+++ b\n", "Update meta title")
    assert "SEO Autopilot Change Proposal" in body
    assert "src/app/page.tsx" in body
    assert "AUDIT_MANIFEST_START" in body

    adapter = GitHubDeploymentAdapter(repo_owner="test-org", repo_name="test-repo", enforce_drift=True)
    request_ok = DeploymentRequest(
        tenant_id=uuid4(),
        site_id=uuid4(),
        proposal_id=uuid4(),
        target_type="github_file",
        target_path="src/app/page.tsx",
        diff_unified="diff",
        base_hash=manifest.base_hash,
        after_content="new",
        idempotency_key="idemp-12345",
        manifest=manifest,
        current_live_content=None,  # No drift
    )

    result = await adapter.deploy(request_ok)
    assert result.status == "applied"
    assert "https://github.com/test-org/test-repo/pull/" in result.external_ref
    assert result.connector_type == "github"

    # Drift error
    request_drift = DeploymentRequest(
        tenant_id=uuid4(),
        site_id=uuid4(),
        proposal_id=uuid4(),
        target_type="github_file",
        target_path="src/app/page.tsx",
        diff_unified="diff",
        base_hash=manifest.base_hash,
        after_content="new",
        idempotency_key="idemp-12345",
        manifest=manifest,
        current_live_content="modified live content differing from base",
    )
    with pytest.raises(DriftDetectedError):
        await adapter.deploy(request_drift)
