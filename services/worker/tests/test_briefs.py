from uuid import UUID

from app.briefs.build import (
    THIN_CONTENT_WORDS,
    ClusterInput,
    PageInput,
    build_brief,
    label_tokens,
)
from app.briefs.generate import structured_data_types

CLUSTER = UUID("019d0000-0000-7000-8000-0000000000c1")
RUN = UUID("019d0000-0000-7000-8000-0000000000r1".replace("r", "9"))
PAGE = UUID("019d0000-0000-7000-8000-0000000000a1")


def cluster(**overrides) -> ClusterInput:
    values = {
        "cluster_id": CLUSTER,
        "analysis_run_id": RUN,
        "label": "agent ai seo",
        "intent": "informational",
        "answer_engine_candidate": True,
        "impressions": 9950.0,
        "clicks": 168.0,
        "ctr": 0.0169,
        "average_position": 11.4,
        "striking_distance_count": 3,
        "competing_page_count": 2,
        "opportunity_score": 78.0,
        "member_count": 6,
        "query_hashes": ["b" * 64, "a" * 64],
    }
    values.update(overrides)
    return ClusterInput(**values)


def page(**overrides) -> PageInput:
    values = {
        "page_id": PAGE,
        "normalized_url": "https://example.com/seo-agents",
        "title": "Agents",
        "meta_description": None,
        "h1": ["Agents"],
        "word_count": 120,
        "structured_data_types": ["Article"],
        "inbound_internal_links": 1,
        "http_status": 200,
        "canonical_url": "https://example.com/seo-agents",
        "robots_directives": [],
    }
    values.update(overrides)
    return PageInput(**values)


def section_keys(brief) -> list[str]:
    return [item.key for item in brief.sections]


def test_a_cluster_with_no_ranking_page_produces_a_new_page_brief() -> None:
    brief = build_brief(cluster(), None)
    assert brief.kind == "new_page"
    assert brief.target_page_id is None
    assert "answer_engine" in section_keys(brief)


def test_a_ranking_page_produces_a_refresh_brief_naming_each_gap() -> None:
    brief = build_brief(cluster(), page())
    assert brief.kind == "refresh"
    assert brief.target_page_id == PAGE
    keys = section_keys(brief)
    # Short title missing cluster tokens, no description, thin body, no FAQ
    # schema, one inbound link, and two pages competing.
    assert keys == [
        "target", "title", "description", "heading",
        "depth", "answer_engine", "internal_links", "consolidation",
    ]


def test_a_healthy_page_produces_only_the_context_section() -> None:
    healthy = page(
        title="A complete guide to the seo ai agent workflow",
        meta_description=(
            "How an seo ai agent audits a site, clusters demand, and prepares briefs "
            "your team can act on this week."
        ),
        h1=["The seo ai agent workflow"],
        word_count=THIN_CONTENT_WORDS + 500,
        structured_data_types=["Article", "FAQPage"],
        inbound_internal_links=9,
    )
    brief = build_brief(cluster(competing_page_count=1), healthy)
    assert section_keys(brief) == ["target"]
    # No gaps means the brief adds nothing to the cluster's own score.
    assert brief.priority_score == 78.0


def test_priority_rises_with_gap_count_and_stays_bounded() -> None:
    gapless = build_brief(cluster(competing_page_count=1), page(
        title="A complete guide to the seo ai agent workflow",
        meta_description="x" * 100, h1=["The seo ai agent workflow"],
        word_count=2000, structured_data_types=["FAQPage"], inbound_internal_links=9,
    ))
    gappy = build_brief(cluster(), page())
    assert gappy.priority_score > gapless.priority_score
    assert build_brief(cluster(opportunity_score=100.0), page()).priority_score == 100.0


def test_the_brief_stores_no_readable_query_term() -> None:
    brief = build_brief(cluster(), page())
    serialized = repr(brief)
    # Members are referenced by hash; only the shared cluster tokens are text.
    assert brief.query_hashes == sorted(["b" * 64, "a" * 64])
    assert "seo ai agent pricing" not in serialized
    for section in brief.sections:
        assert "query_terms" not in section.evidence


def test_the_same_evidence_reproduces_the_same_content_hash() -> None:
    first = build_brief(cluster(), page())
    second = build_brief(cluster(), page())
    assert first.content_hash == second.content_hash
    # A changed observation changes the hash.
    assert build_brief(cluster(), page(word_count=5000)).content_hash != first.content_hash


def test_label_tokens_drive_coverage_checks() -> None:
    assert label_tokens("agent ai seo") == ["agent", "ai", "seo"]
    missing = build_brief(cluster(), page(title="Agents")).sections[1]
    assert missing.key == "title"
    assert set(missing.evidence["missing_tokens"]) == {"ai", "seo"}


def test_structured_data_types_survive_hostile_shapes() -> None:
    assert structured_data_types('[{"@type":"FAQPage"}]') == ["FAQPage"]
    assert structured_data_types([{"@type": ["Article", "BlogPosting"]}]) == [
        "Article", "BlogPosting"
    ]
    assert structured_data_types("not json") == []
    assert structured_data_types([{"@type": 42}, {"no_type": True}]) == []
    # Deeply nested input is bounded rather than followed forever.
    nested: object = {"@type": "Deep"}
    for _ in range(50):
        nested = {"child": nested}
    assert structured_data_types(nested) == []
