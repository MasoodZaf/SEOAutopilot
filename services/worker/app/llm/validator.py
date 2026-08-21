from collections.abc import Iterable

from app.llm.base import BudgetExceededError, EvidenceValidationError


def validate_evidence_citations(
    cited_refs: Iterable[str],
    allowed_evidence_refs: set[str],
) -> None:
    """Ensures every citation in the LLM's structured output corresponds to a verified input evidence ID."""
    hallucinated: list[str] = []
    for ref in cited_refs:
        if ref not in allowed_evidence_refs:
            hallucinated.append(ref)

    if hallucinated:
        raise EvidenceValidationError(
            f"LLM produced ungrounded/hallucinated evidence references: {hallucinated}"
        )


def enforce_cost_budget(cost_micros: int, max_budget_micros: int) -> None:
    """Enforces token/cost micro ceiling for tenant operations."""
    if cost_micros > max_budget_micros:
        raise BudgetExceededError(
            f"Operation cost ({cost_micros} micros) exceeds allowed budget ({max_budget_micros} micros)."
        )
