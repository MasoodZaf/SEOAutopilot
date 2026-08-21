from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VerificationResult:
    is_verified: bool
    observed_snippet: str
    http_status: int
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_rendered_content(
    expected_pattern: str,
    live_body: str,
    live_status: int,
) -> VerificationResult:
    """Verifies that the deployed proposal's expected pattern appears in the live HTML response."""
    if live_status >= 400:
        return VerificationResult(
            is_verified=False,
            observed_snippet=f"HTTP {live_status}",
            http_status=live_status,
            notes=f"Target page returned error status {live_status}.",
        )

    clean_pattern = expected_pattern.strip().lower()
    clean_body = live_body.lower()

    if clean_pattern in clean_body:
        idx = clean_body.find(clean_pattern)
        start = max(0, idx - 40)
        end = min(len(live_body), idx + len(clean_pattern) + 40)
        snippet = live_body[start:end].strip()
        return VerificationResult(
            is_verified=True,
            observed_snippet=snippet,
            http_status=live_status,
            notes="Rendered verification passed. Expected pattern observed in live HTML.",
        )
    else:
        # Check partial token match
        tokens = [t for t in clean_pattern.split() if len(t) > 3]
        matched_tokens = [t for t in tokens if t in clean_body]
        notes = (
            f"Pattern not found verbatim. Partial token match: {len(matched_tokens)}/{len(tokens)} tokens."
            if tokens
            else "Pattern not found."
        )
        return VerificationResult(
            is_verified=False,
            observed_snippet=live_body[:200].strip(),
            http_status=live_status,
            notes=notes,
        )


@dataclass(frozen=True, slots=True)
class MetricDeltaSummary:
    clicks_baseline: float
    clicks_followup: float
    clicks_delta: float
    clicks_pct_change: float | None

    impressions_baseline: float
    impressions_followup: float
    impressions_delta: float
    impressions_pct_change: float | None

    ctr_baseline: float | None
    ctr_followup: float | None
    ctr_delta: float | None

    position_baseline: float | None
    position_followup: float | None
    position_delta: float | None

    confidence_score: float
    is_sparse: bool
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_measurement_delta(
    baseline: dict[str, Any],
    followup: dict[str, Any],
) -> MetricDeltaSummary:
    """Calculates empirical before/after delta and confidence over a 28-day window."""
    clicks_b = float(baseline.get("clicks", 0.0))
    clicks_f = float(followup.get("clicks", 0.0))
    clicks_delta = clicks_f - clicks_b
    clicks_pct = ((clicks_delta / clicks_b) * 100) if clicks_b > 0 else None

    imp_b = float(baseline.get("impressions", 0.0))
    imp_f = float(followup.get("impressions", 0.0))
    imp_delta = imp_f - imp_b
    imp_pct = ((imp_delta / imp_b) * 100) if imp_b > 0 else None

    ctr_b = float(baseline["ctr"]) if baseline.get("ctr") is not None else None
    ctr_f = float(followup["ctr"]) if followup.get("ctr") is not None else None
    ctr_delta = (ctr_f - ctr_b) if (ctr_f is not None and ctr_b is not None) else None

    pos_b = float(baseline["position"]) if baseline.get("position") is not None else None
    pos_f = float(followup["position"]) if followup.get("position") is not None else None
    # In search positions, lower is better, so position_delta = pos_b - pos_f (positive = improved)
    pos_delta = (pos_b - pos_f) if (pos_b is not None and pos_f is not None) else None

    is_sparse = imp_b < 100 or imp_f < 100

    if is_sparse:
        confidence = 0.35
    elif imp_b > 1000 and imp_f > 1000:
        confidence = 0.90
    else:
        confidence = 0.70

    caveats = [
        "Observed performance represents empirical association over the 28-day window and does not prove guaranteed ranking causation.",
    ]
    if is_sparse:
        caveats.append("Data volume is sparse (< 100 impressions). Statistical variance may be high.")

    return MetricDeltaSummary(
        clicks_baseline=clicks_b,
        clicks_followup=clicks_f,
        clicks_delta=round(clicks_delta, 2),
        clicks_pct_change=round(clicks_pct, 2) if clicks_pct is not None else None,
        impressions_baseline=imp_b,
        impressions_followup=imp_f,
        impressions_delta=round(imp_delta, 2),
        impressions_pct_change=round(imp_pct, 2) if imp_pct is not None else None,
        ctr_baseline=round(ctr_b, 4) if ctr_b is not None else None,
        ctr_followup=round(ctr_f, 4) if ctr_f is not None else None,
        ctr_delta=round(ctr_delta, 4) if ctr_delta is not None else None,
        position_baseline=round(pos_b, 2) if pos_b is not None else None,
        position_followup=round(pos_f, 2) if pos_f is not None else None,
        position_delta=round(pos_delta, 2) if pos_delta is not None else None,
        confidence_score=confidence,
        is_sparse=is_sparse,
        caveats=caveats,
    )
