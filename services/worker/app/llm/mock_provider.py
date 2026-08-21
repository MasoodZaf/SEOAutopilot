import hashlib
import json
from typing import Any, TypeVar

from app.llm.base import PolicyContext, StructuredRequest, StructuredResult
from app.llm.sanitizer import detect_and_guard_injection
from app.llm.validator import enforce_cost_budget, validate_evidence_citations
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def stable_hash(*parts: object) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class MockLLMProvider:
    """Deterministic mock provider for reproducible test runs and offline evaluations."""

    def __init__(
        self,
        default_cost_micros: int = 500,
        fixed_output: dict[str, Any] | None = None,
        simulate_injection_check: bool = True,
    ) -> None:
        self.default_cost_micros = default_cost_micros
        self.fixed_output = fixed_output
        self.simulate_injection_check = simulate_injection_check

    async def generate_structured(
        self,
        request: StructuredRequest,
        response_schema: type[T],
        policy: PolicyContext | None = None,
    ) -> StructuredResult:
        # Check budget limits
        budget_limit = policy.max_cost_micros_ceiling if policy else request.max_cost_micros
        enforce_cost_budget(self.default_cost_micros, budget_limit)

        # Check prompt injection on user and system inputs if configured
        if self.simulate_injection_check:
            detect_and_guard_injection(request.user_prompt, strict=True)
            detect_and_guard_injection(request.system_instructions, strict=True)

        input_hash = stable_hash(
            request.task,
            request.prompt_version,
            request.evidence_refs,
            request.user_prompt,
        )

        output_dict: dict[str, Any]
        if self.fixed_output is not None:
            output_dict = self.fixed_output
        else:
            # Generate deterministic fallback matching schema fields
            output_dict = {}
            for field_name, field_info in response_schema.model_fields.items():
                annotation = field_info.annotation
                if annotation is str or annotation == str:
                    output_dict[field_name] = f"Deterministic recommendation for {field_name}"
                elif annotation is int or annotation == int:
                    output_dict[field_name] = 1
                elif annotation is float or annotation == float:
                    output_dict[field_name] = 0.85
                elif annotation is list[str] or getattr(annotation, "__origin__", None) is list:
                    output_dict[field_name] = request.evidence_refs[:1] if request.evidence_refs else []
                elif annotation is dict or getattr(annotation, "__origin__", None) is dict:
                    output_dict[field_name] = {"status": "ok"}
                else:
                    output_dict[field_name] = None

        # Validate structured output against response_schema
        validated_instance = response_schema.model_validate(output_dict)
        final_output = validated_instance.model_dump()

        # Validate evidence references cited in output
        cited_in_output = final_output.get("evidence_refs", [])
        if isinstance(cited_in_output, list) and cited_in_output:
            validate_evidence_citations(cited_in_output, set(request.evidence_refs))

        output_hash = stable_hash(final_output)

        return StructuredResult(
            provider="mock",
            model=request.model,
            output=final_output,
            evidence_refs=request.evidence_refs,
            input_hash=input_hash,
            output_hash=output_hash,
            cost_micros=self.default_cost_micros,
            prompt_tokens=42,
            completion_tokens=18,
            latency_ms=12,
        )
