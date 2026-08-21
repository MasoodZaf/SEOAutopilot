from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScoreFactors:
    impact: float
    confidence: float
    urgency: float
    effort: float
    risk: str


RISK = {"low": 1.0, "medium": 0.75, "high": 0.35, "prohibited": 0.0}


def opportunity_score(f: ScoreFactors) -> float:
    if any(v < 0 or v > 1 for v in (f.impact, f.confidence, f.urgency, f.effort)):
        raise ValueError("factors must be 0..1")
    if f.risk not in RISK:
        raise ValueError("unknown risk")
    raw = f.impact * f.confidence * f.urgency / (0.25 + 0.75 * f.effort)
    return round(min(100.0, raw * 100 * RISK[f.risk]), 2)
