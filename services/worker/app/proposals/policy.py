from dataclasses import asdict, dataclass
from uuid import UUID

from app.proposals.validator import ValidationCheck


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    risk: str
    requires_approval: bool
    required_approver_count: int
    allowed_roles: list[str]
    separation_of_duties_enforced: bool
    can_auto_deploy: bool
    rejection_reasons: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_proposal_policy(
    target_type: str,
    target_path: str,
    before_content: str,
    after_content: str,
    validations: list[ValidationCheck],
    author_id: UUID | None = None,
    tenant_mode: str = "recommend",
) -> PolicyDecision:
    """Evaluates risk classification, approver rules, and deployment eligibility."""
    rejection_reasons: list[str] = []

    # Check for failed validations
    for val in validations:
        if not val.passed:
            rejection_reasons.append(f"Validation failed: {val.name} ({val.message})")

    # Risk level resolution
    if any(val.name == "prohibited_claim_safety" and not val.passed for val in validations):
        risk = "prohibited"
    elif "robots.txt" in target_path.lower() or "sitemap" in target_path.lower():
        risk = "high"
    elif target_type in {"html_meta", "json_ld_schema", "link_insertion"}:
        risk = "low"
    elif target_type in {"content_edit", "github_file"}:
        # Content changes of > 200 chars or high-impact edits
        risk = "medium" if abs(len(after_content) - len(before_content)) < 300 else "high"
    else:
        risk = "medium"

    if risk == "prohibited":
        rejection_reasons.append("Prohibited change class cannot be deployed.")

    requires_approval = True
    required_approver_count = 2 if risk in {"medium", "high"} else 1
    allowed_roles = ["owner", "admin", "seo_manager", "editor"] if risk == "low" else ["owner", "admin", "seo_manager"]
    separation_of_duties_enforced = True
    can_auto_deploy = tenant_mode == "autopilot" and risk == "low" and not rejection_reasons

    return PolicyDecision(
        risk=risk,
        requires_approval=requires_approval,
        required_approver_count=required_approver_count,
        allowed_roles=allowed_roles,
        separation_of_duties_enforced=separation_of_duties_enforced,
        can_auto_deploy=can_auto_deploy,
        rejection_reasons=rejection_reasons,
    )
