from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

AUTOPILOT_ALLOWLISTED_TARGET_TYPES = {"html_meta", "json_ld_schema", "link_insertion"}


@dataclass(frozen=True, slots=True)
class AutopilotEligibility:
    is_eligible: bool
    reason: str
    risk: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_autopilot_execution_eligibility(
    site_mode: str,
    autopilot_enabled: bool,
    emergency_freeze: bool,
    daily_change_budget: int,
    freeze_window_start: datetime | None,
    freeze_window_end: datetime | None,
    today_deployments_count: int,
    proposal_risk: str,
    proposal_target_type: str,
    now: datetime,
) -> AutopilotEligibility:
    """Evaluates whether an automated deployment can proceed under current governance and safety rules."""
    if emergency_freeze:
        return AutopilotEligibility(False, "emergency_freeze_active", proposal_risk)

    if site_mode != "autopilot" or not autopilot_enabled:
        return AutopilotEligibility(False, "autopilot_disabled_for_site", proposal_risk)

    if freeze_window_start and freeze_window_end and (freeze_window_start <= now <= freeze_window_end):
        return AutopilotEligibility(False, "scheduled_freeze_window_active", proposal_risk)

    if today_deployments_count >= daily_change_budget:
        return AutopilotEligibility(
            False,
            f"daily_change_budget_exhausted ({today_deployments_count}/{daily_change_budget})",
            proposal_risk,
        )

    if proposal_risk != "low":
        return AutopilotEligibility(
            False,
            f"non_low_risk_{proposal_risk}_requires_human_approval",
            proposal_risk,
        )

    if proposal_target_type not in AUTOPILOT_ALLOWLISTED_TARGET_TYPES:
        return AutopilotEligibility(
            False,
            f"target_type_{proposal_target_type}_not_allowlisted_for_autopilot",
            proposal_risk,
        )

    return AutopilotEligibility(True, "eligible_for_autopilot_deployment", proposal_risk)


def simulate_policy_on_proposals(
    site_mode: str,
    autopilot_enabled: bool,
    emergency_freeze: bool,
    daily_change_budget: int,
    freeze_window_start: datetime | None,
    freeze_window_end: datetime | None,
    proposals: list[Any],
    now: datetime,
) -> dict[str, Any]:
    """Runs a dry-run policy simulation across a set of proposals."""
    auto_deployable = 0
    review_required = 0
    prohibited = 0
    results: list[dict[str, Any]] = []

    for i, p in enumerate(proposals):
        eligibility = check_autopilot_execution_eligibility(
            site_mode=site_mode,
            autopilot_enabled=autopilot_enabled,
            emergency_freeze=emergency_freeze,
            daily_change_budget=daily_change_budget,
            freeze_window_start=freeze_window_start,
            freeze_window_end=freeze_window_end,
            today_deployments_count=min(i, daily_change_budget),
            proposal_risk=p.risk,
            proposal_target_type=p.target_type,
            now=now,
        )
        if p.risk == "prohibited":
            prohibited += 1
            category = "prohibited"
        elif eligibility.is_eligible:
            auto_deployable += 1
            category = "auto_deployable"
        else:
            review_required += 1
            category = "review_required"

        results.append(
            {
                "proposal_id": str(p.id),
                "title": p.title,
                "risk": p.risk,
                "target_type": p.target_type,
                "category": category,
                "eligibility_reason": eligibility.reason,
            }
        )

    return {
        "evaluated_proposals_count": len(proposals),
        "auto_deployable_count": auto_deployable,
        "review_required_count": review_required,
        "prohibited_count": prohibited,
        "results": results,
    }
