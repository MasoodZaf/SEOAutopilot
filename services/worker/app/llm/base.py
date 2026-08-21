from dataclasses import dataclass
from typing import Any, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class EvidenceValidationError(Exception):
    """Raised when an LLM proposal or finding cites hallucinated/unverified evidence."""


class BudgetExceededError(Exception):
    """Raised when an LLM operation exceeds the allocated token or cost micro ceiling."""


class PromptInjectionError(Exception):
    """Raised when hostile prompt injection or instruction escape is detected."""


@dataclass(frozen=True, slots=True)
class PolicyContext:
    tenant_id: UUID
    site_id: UUID
    mode: str = "observe"
    max_cost_micros_ceiling: int = 50_000
    allow_external_calls: bool = False


class StructuredRequest(BaseModel):
    task: str
    evidence_refs: list[str] = Field(default_factory=list)
    prompt_version: str = "v1"
    system_instructions: str = ""
    user_prompt: str = ""
    max_cost_micros: int = 20_000
    model: str = "gpt-4o-mini"


class StructuredResult(BaseModel):
    provider: str
    model: str
    output: dict[str, Any]
    evidence_refs: list[str]
    input_hash: str
    output_hash: str
    cost_micros: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class LLMProvider(Protocol):
    async def generate_structured(
        self,
        request: StructuredRequest,
        response_schema: type[T],
        policy: PolicyContext | None = None,
    ) -> StructuredResult: ...
