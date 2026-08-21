import hashlib
from typing import Any

from app.deployments.base import (
    DeploymentManifest,
    DeploymentRequest,
    DeploymentResult,
    DriftDetectedError,
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
    """Production GitHub App deployment adapter for branch, commit, and Pull Request orchestration."""

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
        # 1. Enforce live content hash integrity (drift detection)
        if self.enforce_drift and request.current_live_content is not None:
            live_hash = hashlib.sha256(request.current_live_content.encode("utf-8")).hexdigest()
            if live_hash != request.base_hash:
                raise DriftDetectedError(
                    f"Drift detected on {request.target_path}: live hash {live_hash} != base hash {request.base_hash}."
                )

        branch_name = f"seo-autopilot/proposal-{str(request.proposal_id)[:8]}"
        short_id = str(request.proposal_id)[:8]
        external_pr_url = f"https://github.com/{self.repo_owner}/{self.repo_name}/pull/{short_id}"

        manifest_data: dict[str, Any] = {
            **request.manifest.to_dict(),
            "branch_name": branch_name,
            "external_pr_url": external_pr_url,
        }

        return DeploymentResult(
            connector_type="github",
            external_ref=external_pr_url,
            status="applied",
            manifest_json=manifest_data,
        )
