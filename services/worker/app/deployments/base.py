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
class DeploymentResult:
    connector_type: str
    external_ref: str
    status: str
    manifest_json: dict[str, Any]


class DeploymentAdapter(Protocol):
    async def deploy(self, request: DeploymentRequest) -> DeploymentResult: ...
