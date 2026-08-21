import hashlib

from app.deployments.base import (
    DeploymentRequest,
    DeploymentResult,
    DriftDetectedError,
)


class MockDeploymentAdapter:
    """Deterministic mock deployment adapter for PR creation and staging verification."""

    def __init__(self, connector_type: str = "github", enforce_drift: bool = True) -> None:
        self.connector_type = connector_type
        self.enforce_drift = enforce_drift

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        # Check drift if current_live_content is supplied
        if self.enforce_drift and request.current_live_content is not None:
            live_hash = hashlib.sha256(request.current_live_content.encode("utf-8")).hexdigest()
            if live_hash != request.base_hash:
                raise DriftDetectedError(
                    f"Drift detected: target live hash {live_hash} does not match proposal base hash {request.base_hash}."
                )

        if self.connector_type == "github":
            short_id = str(request.proposal_id)[:8]
            external_ref = f"https://github.com/synthetic-pilot/codearc/pull/{short_id}"
        elif self.connector_type == "cms_staging":
            external_ref = f"cms-revision://staging/{request.proposal_id}"
        else:
            external_ref = f"mock://receipt/{request.idempotency_key}"

        return DeploymentResult(
            connector_type=self.connector_type,
            external_ref=external_ref,
            status="applied",
            manifest_json=request.manifest.to_dict(),
        )
