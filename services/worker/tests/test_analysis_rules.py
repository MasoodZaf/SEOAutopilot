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
        normalized_url=str(overrides.pop("normalized_url", "https://example.com/page")),
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


def test_a_plural_in_the_title_is_not_a_mismatch_with_its_singular_in_the_heading() -> None:
    """TheCalcHive's front page, which the unstemmed rule reported as a mismatch.

    "Free Online Calculators" and "Every calculator you'll ever need" are the
    same subject. Flagging it invited a proposal to replace a hand-written hero
    headline with a brand string.
    """
    evidence = multiagent_evidence(
        page=page_evidence(
            title="CalcHive — Free Online Calculators & Financial Tools",
            h1=["Every calculator you'll ever need"],
        )
    )
    _, findings = evaluate_multiagent_page(evidence)
    assert "content.title_h1_mismatch" not in [f.code for f in findings]


def test_a_genuine_topical_mismatch_still_fires() -> None:
    evidence = multiagent_evidence(
        page=page_evidence(
            title="Zodiac & Birth Chart — Free Online Tool",
            h1=["Every calculator you'll ever need"],
        )
    )
    _, findings = evaluate_multiagent_page(evidence)
    assert "content.title_h1_mismatch" in [f.code for f in findings]


def test_a_heading_shared_across_the_site_is_reported_with_its_count() -> None:
    evidence = multiagent_evidence(
        page=page_evidence(h1=["Every calculator you'll ever need"], pages_sharing_h1=32)
    )
    _, findings = evaluate_multiagent_page(evidence)
    duplicate = [f for f in findings if f.code == "h1.duplicate_across_site"]
    assert len(duplicate) == 1
    # The count is in the summary, so the claim carries its own evidence.
    assert "32 pages" in duplicate[0].summary
    assert duplicate[0].severity == "medium"


def test_a_heading_shared_by_only_two_pages_is_left_alone() -> None:
    evidence = multiagent_evidence(page=page_evidence(pages_sharing_h1=2))
    _, findings = evaluate_multiagent_page(evidence)
    assert "h1.duplicate_across_site" not in [f.code for f in findings]


def test_a_unique_heading_is_never_reported_as_duplicated() -> None:
    _, findings = evaluate_multiagent_page(multiagent_evidence())
    assert "h1.duplicate_across_site" not in [f.code for f in findings]


# --- content.title_omits_url_topic ------------------------------------------


def codes(evidence: MultiAgentPageEvidence) -> set[str]:
    _score, findings = evaluate_multiagent_page(evidence)
    return {finding.code for finding in findings}


def test_a_page_that_never_says_what_its_url_says_it_is() -> None:
    """The pilot site's shape: /networth-calculator titled "Net Worth Tracker".

    The slug is the one description of a page its author chose deliberately and
    no template overwrites, so it can carry this finding without any search
    data to back it.
    """
    found = codes(
        multiagent_evidence(
            normalized_url="https://thecalchive.com/networth-calculator",
            page=page_evidence(title="Net Worth Tracker — Free Tool", h1=["Net Worth Tracker"]),
        )
    )
    assert "content.title_omits_url_topic" in found


def test_the_word_in_either_place_is_enough() -> None:
    """A title without it but a heading with it is a weaker, different problem."""
    for title, heading in (
        ("Net Worth Calculator — Free Tool", "Net Worth Tracker"),
        ("Net Worth Tracker — Free Tool", "Net Worth Calculator"),
    ):
        found = codes(
            multiagent_evidence(
                normalized_url="https://thecalchive.com/networth-calculator",
                page=page_evidence(title=title, h1=[heading]),
            )
        )
        assert "content.title_omits_url_topic" not in found


def test_the_match_is_stemmed_like_every_other_token_comparison() -> None:
    found = codes(
        multiagent_evidence(
            normalized_url="https://thecalchive.com/networth-calculator",
            page=page_evidence(title="Net Worth Calculators — Free Tool", h1=["Net Worth"]),
        )
    )
    assert "content.title_omits_url_topic" not in found


def test_a_section_slug_is_not_a_subject_claim() -> None:
    """`/about` titled "Our Story" is fine, and reporting it would bury the rest."""
    for url in ("https://example.com/about", "https://example.com/", "https://example.com/blog"):
        found = codes(
            multiagent_evidence(
                normalized_url=url,
                page=page_evidence(title="Our Story — Example", h1=["Our Story"]),
            )
        )
        assert "content.title_omits_url_topic" not in found


def test_a_modifier_the_slug_ran_together_is_not_a_missing_word() -> None:
    """`/networth-calculator` against "Net Worth Calculator" is not a defect.

    Slugs concatenate what prose separates. Checking every slug word reported a
    missing "networth" on a page whose title says "Net Worth", which is the
    kind of false positive that teaches people to ignore an audit.
    """
    found = codes(
        multiagent_evidence(
            normalized_url="https://thecalchive.com/networth-calculator",
            page=page_evidence(title="Net Worth Calculator — Free", h1=["Net Worth Calculator"]),
        )
    )
    assert "content.title_omits_url_topic" not in found


def test_only_one_finding_per_page() -> None:
    _score, findings = evaluate_multiagent_page(
        multiagent_evidence(
            normalized_url="https://example.com/annual-percentage-calculator",
            page=page_evidence(title="Rate Tool — Example", h1=["Rate Tool"]),
        )
    )
    raised = [f for f in findings if f.code == "content.title_omits_url_topic"]
    assert len(raised) == 1


def test_the_finding_names_the_word_the_author_wrote_not_its_stem() -> None:
    """A finding that says a page omits "calculat" reads like a broken auditor."""
    _score, findings = evaluate_multiagent_page(
        multiagent_evidence(
            normalized_url="https://thecalchive.com/networth-calculator",
            page=page_evidence(title="Net Worth Tracker — Free", h1=["Net Worth Tracker"]),
        )
    )
    raised = next(f for f in findings if f.code == "content.title_omits_url_topic")
    assert "'calculator'" in raised.summary
    assert "calculat'" not in raised.summary.replace("calculator'", "")


def rendering_codes(**page: object) -> list[str]:
    _, findings = evaluate_multiagent_page(multiagent_evidence(page=page_evidence(**page)))
    return [finding.code for finding in findings if finding.code.startswith("rendering.")]


def test_a_page_that_only_exists_after_javascript_is_reported_with_both_counts() -> None:
    # codearc.net's shape: the HTML says "Please enable JavaScript", the
    # browser produces a full challenge.
    _, findings = evaluate_multiagent_page(
        multiagent_evidence(page=page_evidence(word_count=261, server_word_count=6))
    )
    [finding] = [f for f in findings if f.code == "rendering.content_requires_javascript"]
    assert finding.severity == "high"
    assert finding.risk == "high"
    assert "6 of its 261 words" in finding.summary


def test_a_page_that_was_not_rendered_is_never_reported() -> None:
    assert rendering_codes(word_count=500, server_word_count=None) == []


def test_server_html_carrying_the_substance_is_not_reported() -> None:
    # Scripts adding a widget to a page that is already there.
    assert rendering_codes(word_count=500, server_word_count=400) == []
    assert rendering_codes(word_count=500, server_word_count=100) == []
    assert rendering_codes(word_count=500, server_word_count=99) == [
        "rendering.content_requires_javascript"
    ]


def test_a_page_short_in_both_forms_is_thin_not_client_rendered() -> None:
    assert rendering_codes(word_count=60, server_word_count=0) == []
