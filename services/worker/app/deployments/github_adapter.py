from app.deployments.base import (
    DeploymentBlockedError,
    DeploymentManifest,
    DeploymentRequest,
    DeploymentResult,
)


def format_pr_body(manifest: DeploymentManifest, diff_unified: str, rationale: str) -> str:
    """Formats an auditable, machine-readable pull request description."""
    return f"""## 🚀 SEO Autopilot Change Proposal

**Proposal ID**: `{manifest.proposal_id}`
**Target Path**: `{manifest.target_path}`
**Base Hash**: `{manifest.base_hash}`
**Proposal Hash**: `{manifest.proposal_hash}`
**Author ID**: `{manifest.author_id}`
**Approved By**: `{", ".join(manifest.approver_ids) or "System (Autopilot)"}`

---

### 📝 Change Rationale
{rationale}

### 🔍 Unified Diff Summary
```diff
{diff_unified}
```

---
<!-- AUDIT_MANIFEST_START
{manifest.to_dict()}
AUDIT_MANIFEST_END -->
"""


class GitHubDeploymentAdapter:
    """Unwired GitHub adapter contract; it must not report an external deployment."""

    def __init__(
        self,
        repo_owner: str = "synthetic-pilot",
        repo_name: str = "codearc",
        auth_token: str | None = None,
        enforce_drift: bool = True,
    ) -> None:
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.auth_token = auth_token
        self.enforce_drift = enforce_drift

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        _ = request
        raise DeploymentBlockedError("github_connector_not_implemented")
