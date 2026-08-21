from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DifferenceInDifferencesResult:
    target_baseline: float
    target_followup: float
    target_delta: float
    control_baseline_avg: float
    control_followup_avg: float
    control_delta_avg: float
    net_treatment_effect: float
    control_urls_count: int
    is_statistically_significant: bool
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_synthetic_control_diff_in_diff(
    target_baseline: float,
    target_followup: float,
    control_baselines: list[float],
    control_followups: list[float],
) -> DifferenceInDifferencesResult:
    """Calculates Difference-in-Differences treatment effect using an unchanged synthetic control group on the same domain."""
    target_delta = target_followup - target_baseline

    if not control_baselines or not control_followups or len(control_baselines) != len(control_followups):
        return DifferenceInDifferencesResult(
            target_baseline=round(target_baseline, 2),
            target_followup=round(target_followup, 2),
            target_delta=round(target_delta, 2),
            control_baseline_avg=0.0,
            control_followup_avg=0.0,
            control_delta_avg=0.0,
            net_treatment_effect=round(target_delta, 2),
            control_urls_count=0,
            is_statistically_significant=False,
            explanation="No control group available. Net treatment effect equals raw target delta.",
        )

    ctrl_b_avg = sum(control_baselines) / len(control_baselines)
    ctrl_f_avg = sum(control_followups) / len(control_followups)
    ctrl_delta_avg = ctrl_f_avg - ctrl_b_avg

    # Difference-in-Differences: net change attributable to the deployed modification
    net_treatment = target_delta - ctrl_delta_avg

    # Statistical significance heuristic based on sample size and relative effect size
    is_significant = len(control_baselines) >= 3 and abs(net_treatment) > (abs(ctrl_delta_avg) * 0.25 + 5.0)

    if net_treatment > 0:
        exp = (
            f"Target URL outperformed control group by +{round(net_treatment, 2)} units "
            f"after adjusting for site-wide background trend ({round(ctrl_delta_avg, 2)})."
        )
    elif net_treatment < 0:
        exp = (
            f"Target URL underperformed control group by {round(net_treatment, 2)} units "
            f"relative to site-wide background trend ({round(ctrl_delta_avg, 2)})."
        )
    else:
        exp = "Target URL performed in lockstep with the control group."

    return DifferenceInDifferencesResult(
        target_baseline=round(target_baseline, 2),
        target_followup=round(target_followup, 2),
        target_delta=round(target_delta, 2),
        control_baseline_avg=round(ctrl_b_avg, 2),
        control_followup_avg=round(ctrl_f_avg, 2),
        control_delta_avg=round(ctrl_delta_avg, 2),
        net_treatment_effect=round(net_treatment, 2),
        control_urls_count=len(control_baselines),
        is_statistically_significant=is_significant,
        explanation=exp,
    )
