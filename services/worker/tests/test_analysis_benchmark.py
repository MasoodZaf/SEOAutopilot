from app.analysis_rules import (
    LinkGraphEvidence,
    MultiAgentPageEvidence,
    PageEvidence,
    PerformanceEvidence,
    SearchConsoleEvidence,
    evaluate_multiagent_page,
    opportunity_score,
)


def create_seeded_synthetic_evidence(index: int) -> MultiAgentPageEvidence:
    """Creates a deterministic synthetic page observation across all evidence dimensions."""
    status = 500 if index == 0 else (404 if index == 1 else (301 if index == 2 else 200))
    title = None if index == 3 else f"Page Title {index} with adequate length for search indexing"
    meta_desc = None if index == 4 else f"Description {index} explaining the details of the page with sufficient length for snippets."
    h1 = [] if index == 5 else [f"Heading {index}"]
    word_count = 50 if index == 6 else 600
    canonical = None if index == 7 else f"https://example.com/page-{index}"
    structured_data = [] if index == 8 else [{"@type": "Article"}]

    inbound = 0 if index == 9 else 4
    depth = 5 if index == 10 else 2

    striking = 2 if index == 11 else 0
    low_ctr = 1 if index == 12 else 0

    lcp = 4200.0 if index == 13 else 1500.0
    cls_val = 0.30 if index == 14 else 0.02
    perf_score = 45 if index == 15 else 92

    return MultiAgentPageEvidence(
        page_id=f"019d0000-0000-7000-8000-0000000000{index:02d}",
        normalized_url=f"https://example.com/page-{index}",
        page=PageEvidence(
            status=status,
            title=title,
            meta_description=meta_desc,
            h1=h1,
            word_count=word_count,
            canonical_url=canonical,
            robots_directives=["index", "follow"],
            structured_data=structured_data,
        ),
        link_graph=LinkGraphEvidence(
            inbound_internal_links=inbound,
            crawl_depth=depth,
        ),
        search_console=SearchConsoleEvidence(
            clicks=10 * index,
            impressions=500 * index,
            ctr=0.01 if low_ctr else 0.05,
            position=14.0 if striking else 4.0,
            striking_distance_queries=striking,
            low_ctr_queries=low_ctr,
        ),
        performance=PerformanceEvidence(
            performance_score=perf_score,
            lcp_ms=lcp,
            cls=cls_val,
        ),
    )


def test_reproducible_top_opportunities_benchmark() -> None:
    """Evaluates 20 distinct synthetic pages twice and verifies 100% deterministic reproducibility."""
    dataset = [create_seeded_synthetic_evidence(i) for i in range(20)]

    def run_scoring() -> list[tuple[str, str, float]]:
        ranked_items: list[tuple[str, str, float]] = []
        for item in dataset:
            _, findings = evaluate_multiagent_page(item)
            for f in findings:
                score = opportunity_score(f)
                ranked_items.append((item.page_id, f.code, score))
        ranked_items.sort(key=lambda x: x[2], reverse=True)
        return ranked_items[:20]

    run1 = run_scoring()
    run2 = run_scoring()

    assert len(run1) == 20
    assert run1 == run2, "Scoring and top 20 ranking must be 100% deterministic and reproducible."
    # Top issues should prioritize critical server errors and client errors
    assert run1[0][1] in {"status.server_error", "title.missing", "linking.broken_links"}
