from app.analysis_rules import (
    LinkGraphEvidence,
    MultiAgentPageEvidence,
    PageEvidence,
    PerformanceEvidence,
    SearchConsoleEvidence,
    evaluate_multiagent_page,
    evaluate_page,
    opportunity_score,
)


def page_evidence(**overrides: object) -> PageEvidence:
    values: dict[str, object] = {
        "status": 200,
        "title": "A clear and descriptive page title",
        "meta_description": "A useful description that clearly explains the page for search users and provides enough detail for a strong result snippet.",
        "h1": ["A clear and descriptive page heading"],
        "word_count": 500,
        "canonical_url": "https://example.com/page",
        "robots_directives": ["index", "follow"],
        "structured_data": [{"@type": "Organization"}],
    }
    values.update(overrides)
    return PageEvidence(**values)  # type: ignore[arg-type]


def multiagent_evidence(**overrides: object) -> MultiAgentPageEvidence:
    page = overrides.pop("page", page_evidence())
    link_graph = overrides.pop("link_graph", LinkGraphEvidence(inbound_internal_links=5))
    search_console = overrides.pop("search_console", SearchConsoleEvidence(clicks=100, impressions=1000, ctr=0.10, position=5.0))
    performance = overrides.pop("performance", PerformanceEvidence(performance_score=90, lcp_ms=1800, inp_ms=100, cls=0.02, ttfb_ms=250))
    return MultiAgentPageEvidence(
        page_id="019d0000-0000-7000-8000-000000000001",
        normalized_url="https://example.com/page",
        page=page,  # type: ignore[arg-type]
        link_graph=link_graph,  # type: ignore[arg-type]
        search_console=search_console,  # type: ignore[arg-type]
        performance=performance,  # type: ignore[arg-type]
    )


def test_healthy_page_has_no_findings() -> None:
    score, findings = evaluate_page(page_evidence())
    assert score == 100
    assert findings == []


def test_multiagent_healthy_page_has_no_findings() -> None:
    score, findings = evaluate_multiagent_page(multiagent_evidence())
    assert score == 100
    assert findings == []


def test_technical_findings_and_ranking_are_deterministic() -> None:
    score, findings = evaluate_page(page_evidence(status=503, title=None))
    assert score == 50
    assert [finding.code for finding in findings] == ["status.server_error", "title.missing"]
    assert opportunity_score(findings[0]) == 100.0
    assert opportunity_score(findings[1]) == 92.4


def test_content_agent_detects_title_h1_mismatch_and_thin_content() -> None:
    evidence = multiagent_evidence(
        page=page_evidence(
            title="Comprehensive Guide to Cloud Storage Services",
            h1=["Cooking pasta in five minutes"],
            word_count=80,
        )
    )
    score, findings = evaluate_multiagent_page(evidence)
    codes = [f.code for f in findings]
    assert "content.title_h1_mismatch" in codes
    assert "content.thin" in codes
    assert score < 100


def test_internal_linking_agent_detects_orphan_and_deep_pages() -> None:
    evidence = multiagent_evidence(
        link_graph=LinkGraphEvidence(inbound_internal_links=0, crawl_depth=5, missing_anchor_inbound_count=2, inbound_broken_links=1)
    )
    _score, findings = evaluate_multiagent_page(evidence)
    codes = [f.code for f in findings]
    assert "linking.orphan_page" in codes
    assert "linking.deep_page" in codes
    assert "linking.missing_anchors" in codes
    assert "linking.broken_links" in codes


def test_keyword_agent_detects_striking_distance_and_ctr_gap() -> None:
    evidence = multiagent_evidence(
        search_console=SearchConsoleEvidence(
            clicks=5,
            impressions=1200,
            ctr=0.004,
            position=4.2,
            striking_distance_queries=3,
            low_ctr_queries=1,
        )
    )
    _score, findings = evaluate_multiagent_page(evidence)
    codes = [f.code for f in findings]
    assert "keyword.striking_distance" in codes
    assert "keyword.ctr_gap" in codes


def test_performance_agent_detects_lcp_and_cls_regressions() -> None:
    evidence = multiagent_evidence(
        performance=PerformanceEvidence(
            performance_score=42,
            lcp_ms=4500,
            inp_ms=600,
            cls=0.32,
            ttfb_ms=950,
        )
    )
    _score, findings = evaluate_multiagent_page(evidence)
    codes = [f.code for f in findings]
    assert "performance.lcp_poor" in codes
    assert "performance.cls_poor" in codes
    assert "performance.inp_poor" in codes
    assert "performance.score_poor" in codes


def test_geo_visibility_agent_detects_missing_structured_data() -> None:
    evidence = multiagent_evidence(
        page=page_evidence(structured_data=[])
    )
    score, findings = evaluate_multiagent_page(evidence)
    codes = [f.code for f in findings]
    assert "geo.missing_structured_data" in codes
    assert score == 90
