from uuid import uuid4

import pytest
from app.deployments.base import (
    DeploymentBlockedError,
    DeploymentManifest,
    DeploymentRequest,
)
from app.deployments.github_adapter import GitHubDeploymentAdapter, format_pr_body


@pytest.mark.asyncio
async def test_github_adapter_formats_manifest_but_fails_closed_until_wired() -> None:
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

    with pytest.raises(DeploymentBlockedError, match="github_connector_not_implemented"):
        await adapter.deploy(request_ok)
