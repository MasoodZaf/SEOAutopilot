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


def changed_lines(diff_unified: str) -> list[str]:
    """The lines a change introduces, as the evidence to look for on the live page.

    Verification used to look for `proposal.after_content` -- the whole file --
    inside the live HTML, which passes only when the served document is
    byte-identical to the source. That is the wrong question twice over: it
    fails on any unrelated later edit to the same file, and it succeeds without
    ever establishing that *this* change is the one that landed.

    The added lines of the diff are what this change actually asserts about the
    page. For an `h1_repair` that is a single `<h1>` element, which is precisely
    what should be visible once the pull request is merged.
    """
    added = []
    for line in diff_unified.splitlines():
        if line.startswith("+++") or not line.startswith("+"):
            continue
        text = line[1:].strip()
        if text:
            added.append(text)
    return added


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

    clean_body = live_body.lower()

    # Every non-blank line of the expected pattern must appear. A single-line
    # pattern is the old containment check unchanged; a multi-line one is now
    # "each of these lines is on the page" rather than "these exact bytes,
    # including their indentation and line endings, appear consecutively",
    # which no served document is obliged to preserve.
    fragments = [line.strip().lower() for line in expected_pattern.splitlines() if line.strip()]
    if not fragments:
        return VerificationResult(
            is_verified=False,
            observed_snippet=live_body[:200].strip(),
            http_status=live_status,
            notes="No expected content to look for.",
        )

    missing = [fragment for fragment in fragments if fragment not in clean_body]
    if not missing:
        first = fragments[0]
        idx = clean_body.find(first)
        start = max(0, idx - 40)
        end = min(len(live_body), idx + len(first) + 40)
        return VerificationResult(
            is_verified=True,
            observed_snippet=live_body[start:end].strip(),
            http_status=live_status,
            notes=(
                "Rendered verification passed. "
                f"{len(fragments)} expected line(s) observed in live HTML."
            ),
        )

    # Two ordinary reasons to land here, and this cannot tell them apart from
    # the page alone, so it claims neither. The first run against production
    # found eleven changes whose pull request *was* merged and whose heading was
    # then overwritten hours later by a better one -- "not merged yet" would
    # have been false for every one of them.
    return VerificationResult(
        is_verified=False,
        observed_snippet=live_body[:200].strip(),
        http_status=live_status,
        notes=(
            f"{len(missing)} of {len(fragments)} expected line(s) absent from the live page. "
            "The change is either not merged yet or has since been superseded."
        ),
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
