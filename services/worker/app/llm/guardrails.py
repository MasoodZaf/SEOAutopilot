"""Request guardrails every LLM provider passes through.

These checks used to sit inside `MockLLMProvider`, so the only provider
enforcing them was the one that never talks to a model, and each new provider
would have had to remember to reimplement them. Here a provider implements
`_generate_structured`; `generate_structured` is the single public entry point
and runs the guards around it, so a provider inherits them instead of being
trusted to repeat them.
"""

from abc import ABC, abstractmethod
from typing import TypeVar

from app.llm.base import PolicyContext, StructuredRequest, StructuredResult
from app.llm.sanitizer import detect_and_guard_injection
from app.llm.validator import enforce_cost_budget, validate_evidence_citations
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class GuardedLLMProvider(ABC):
    """Base for any provider. Satisfies the `LLMProvider` protocol."""

    @abstractmethod
    def estimated_cost_micros(self, request: StructuredRequest) -> int:
        """Cost this call is expected to incur, checked before the call is made."""

    @abstractmethod
    async def _generate_structured(
        self,
        request: StructuredRequest,
        response_schema: type[T],
        policy: PolicyContext | None,
    ) -> StructuredResult: ...

    async def generate_structured(
        self,
        request: StructuredRequest,
        response_schema: type[T],
        policy: PolicyContext | None = None,
    ) -> StructuredResult:
        budget_limit = policy.max_cost_micros_ceiling if policy else request.max_cost_micros
        enforce_cost_budget(self.estimated_cost_micros(request), budget_limit)

        # Crawled pages, analytics dimensions and CMS bodies reach a prompt as
        # data. Neither half of the prompt may carry instructions from them.
        detect_and_guard_injection(request.system_instructions, strict=True)
        detect_and_guard_injection(request.user_prompt, strict=True)

        result = await self._generate_structured(request, response_schema, policy)

        # A provider cannot widen the evidence set it was handed.
        validate_evidence_citations(result.evidence_refs, set(request.evidence_refs))
        return result
