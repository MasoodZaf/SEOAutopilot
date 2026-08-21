import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar


class ModelTier(StrEnum):
    LIGHTWEIGHT = "gemini-2.0-flash"
    FRONTIER = "claude-3-7-sonnet"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    tier: ModelTier
    estimated_tokens: int
    rationale: str


class DynamicModelRouter:
    """Intelligently routes SEO analysis & proposal generation to the most cost-effective LLM tier."""

    LIGHTWEIGHT_CATEGORIES: ClassVar[set[str]] = {
        "html_meta",
        "json_ld_schema",
        "link_insertion",
        "alt_text",
        "heading_structure",
    }

    FRONTIER_CATEGORIES: ClassVar[set[str]] = {
        "keyword_cannibalization",
        "content_pruning",
        "site_architecture_overhaul",
        "semantic_clustering",
    }

    def route(self, category: str, content_length: int) -> RoutingDecision:
        if category in self.FRONTIER_CATEGORIES or content_length > 10_000:
            return RoutingDecision(
                tier=ModelTier.FRONTIER,
                estimated_tokens=int(content_length / 4) + 1000,
                rationale=f"Complex strategic category '{category}' requires frontier reasoning.",
            )

        return RoutingDecision(
            tier=ModelTier.LIGHTWEIGHT,
            estimated_tokens=int(content_length / 4) + 300,
            rationale=f"Standard structural category '{category}' routed to high-efficiency tier.",
        )


class SemanticPromptCache:
    """Caches LLM proposal generation results to prevent redundant token consumption."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    def _compute_key(self, prompt: str, base_content_hash: str) -> str:
        data = f"{prompt}:{base_content_hash}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def get(self, prompt: str, base_content_hash: str) -> dict[str, Any] | None:
        key = self._compute_key(prompt, base_content_hash)
        return self._cache.get(key)

    def set(self, prompt: str, base_content_hash: str, result: dict[str, Any]) -> None:
        key = self._compute_key(prompt, base_content_hash)
        self._cache[key] = result

    def size(self) -> int:
        return len(self._cache)
