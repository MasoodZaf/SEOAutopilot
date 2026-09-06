import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from uuid import UUID


class DriftDetectedError(Exception):
    """Raised when the target page or repository content has drifted from the proposal base hash."""


class DeploymentBlockedError(Exception):
    """Raised when safety policy or kill switches block deployment."""


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    tenant_id: str
    site_id: str
    proposal_id: str
    target_path: str
    base_hash: str
    proposal_hash: str
    author_id: str
    approver_ids: list[str]
    deployed_at: str
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DeploymentRequest:
    tenant_id: UUID
    site_id: UUID
    proposal_id: UUID
    target_type: str
    target_path: str
    diff_unified: str
    base_hash: str
    after_content: str
    idempotency_key: str
    manifest: DeploymentManifest
    current_live_content: str | None = None


@dataclass(frozen=True, slots=True)
class BatchDeploymentRequest:
    """Several approved changes, carried to one place as one review.

    Thirty single-file pull requests describe the same work as one pull request
    touching thirty files, and only the second can be read. Each change keeps
    its own proposal, approval and receipt; what is shared is the branch and the
    review, not the authority.
    """

    tenant_id: UUID
    site_id: UUID
    idempotency_key: str
    changes: tuple[DeploymentRequest, ...]

    def __post_init__(self) -> None:
        if not self.changes:
            raise ValueError("A batch needs at least one change.")
        paths = [change.target_path for change in self.changes]
        if len(set(paths)) != len(paths):
            # Two changes to one file in one branch would silently apply
            # whichever was written last, and the receipt for the other would
            # claim an effect that never happened.
            raise ValueError("A batch cannot contain two changes to the same path.")


@dataclass(frozen=True, slots=True)
class BatchDeploymentResult:
    connector_type: str
    external_ref: str
    status: str
    manifest_json: dict[str, Any]
    applied_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    connector_type: str
    external_ref: str
    status: str
    manifest_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RollbackRequest:
    """Undo one deployment.

    `before_content` is the proposal's own record of what the file held when it
    was written, which is what "undo" has to restore -- not whatever is there
    now, since that may include someone else's later work.
    """

    tenant_id: UUID
    site_id: UUID
    proposal_id: UUID
    target_path: str
    before_content: str
    deployed_hash: str
    external_ref: str
    manifest_json: dict[str, Any]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RollbackResult:
    status: str
    external_ref: str
    restored_hash: str
    detail: str


class BatchDeploymentAdapter(Protocol):
    async def deploy_batch(self, request: BatchDeploymentRequest) -> BatchDeploymentResult: ...


class RollbackAdapter(Protocol):
    async def rollback(self, request: RollbackRequest) -> RollbackResult: ...


class DeploymentAdapter(Protocol):
    async def deploy(self, request: DeploymentRequest) -> DeploymentResult: ...


class MockDeploymentAdapter:
    """Deterministic mock deployment adapter for PR creation and staging verification."""

    def __init__(self, connector_type: str = "github", enforce_drift: bool = True) -> None:
        self.connector_type = connector_type
        self.enforce_drift = enforce_drift

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
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

    async def rollback(self, request: RollbackRequest) -> RollbackResult:
        """Reports what the real adapter would do, without doing anything."""
        return RollbackResult(
            status="applied",
            external_ref=f"mock://rollback/{request.proposal_id}",
            restored_hash=hashlib.sha256(request.before_content.encode("utf-8")).hexdigest(),
            detail="mock_rollback",
        )
