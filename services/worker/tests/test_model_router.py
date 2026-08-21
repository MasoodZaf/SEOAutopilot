from app.llm.router import DynamicModelRouter, ModelTier, SemanticPromptCache


def test_dynamic_model_routing_logic() -> None:
    router = DynamicModelRouter()

    # Simple metadata category -> lightweight tier
    decision_simple = router.route("html_meta", content_length=1200)
    assert decision_simple.tier == ModelTier.LIGHTWEIGHT
    assert "high-efficiency tier" in decision_simple.rationale

    # Complex strategic category -> frontier tier
    decision_complex = router.route("keyword_cannibalization", content_length=5000)
    assert decision_complex.tier == ModelTier.FRONTIER
    assert "frontier reasoning" in decision_complex.rationale

    # Oversized document -> frontier tier
    decision_huge = router.route("html_meta", content_length=25_000)
    assert decision_huge.tier == ModelTier.FRONTIER


def test_semantic_prompt_cache() -> None:
    cache = SemanticPromptCache()
    assert cache.size() == 0

    prompt = "Generate meta title"
    base_hash = "abc12345"
    payload = {"title": "Best Cloud Hosting 2026"}

    cache.set(prompt, base_hash, payload)
    assert cache.size() == 1

    cached_hit = cache.get(prompt, base_hash)
    assert cached_hit == payload

    cached_miss = cache.get(prompt, "different_hash")
    assert cached_miss is None
