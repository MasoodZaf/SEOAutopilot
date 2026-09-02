import hashlib
import random
from uuid import UUID

import pytest
from app.keywords.cluster import (
    QueryMetrics,
    build_clusters,
    classify_intent,
    cluster_label,
    is_question,
    opportunity_score,
    similar,
)
from app.keywords.secrets import open_query, query_aad, seal_query

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
OTHER_SITE = UUID("019d0000-0000-7000-8000-000000000013")
PAGE_A = UUID("019d0000-0000-7000-8000-0000000000a1")
PAGE_B = UUID("019d0000-0000-7000-8000-0000000000a2")


def query(term: str, clicks: float, impressions: float, position: float, page: UUID | None = None):
    return QueryMetrics(
        query_hash=hashlib.sha256(term.encode()).hexdigest(),
        term=term,
        clicks=clicks,
        impressions=impressions,
        position=position,
        best_page_id=page,
    )


SAMPLE = [
    query("seo ai agent", 120, 5000, 8.2, PAGE_A),
    query("ai agents for seo", 30, 2200, 12.4, PAGE_A),
    query("best seo ai agent", 10, 900, 14.1, PAGE_B),
    query("seo ai agent pricing", 5, 400, 9.0, PAGE_B),
    query("what is an seo ai agent", 2, 800, 18.0, PAGE_A),
    query("how do seo agents work", 1, 650, 22.0, PAGE_A),
    query("mongodb performance tuning", 40, 3000, 6.0, PAGE_B),
    query("how to tune mongodb performance", 3, 700, 15.0, PAGE_B),
    query("mongodb index tuning guide", 8, 500, 11.0, PAGE_B),
    query("buy mongodb support", 0, 120, 30.0, None),
]


def cluster_by_label(clusters, needle: str):
    return next(item for item in clusters if needle in item.label)


def test_related_phrasings_land_in_one_cluster() -> None:
    clusters = build_clusters(SAMPLE)
    seo = cluster_by_label(clusters, "seo")
    # All six phrasings of the same topic, including the question forms.
    assert seo.member_count == 6
    assert seo.impressions == 9950

    mongodb = cluster_by_label(clusters, "tuning")
    assert mongodb.member_count == 3
    # A transactional query about the same product is not a tuning keyword.
    assert "buy mongodb support" not in [item.query_hash for item in mongodb.members]


def test_clusters_are_independent_of_row_order() -> None:
    baseline = build_clusters(SAMPLE)
    shuffled = SAMPLE[:]
    random.Random(11).shuffle(shuffled)
    replayed = build_clusters(shuffled)
    assert [(c.cluster_key, c.member_count, c.opportunity_score) for c in baseline] == [
        (c.cluster_key, c.member_count, c.opportunity_score) for c in replayed
    ]


def test_answer_engine_candidacy_needs_a_real_share_of_questions() -> None:
    clusters = build_clusters(SAMPLE)
    # Two of six seo queries are question-form: above the 30% share.
    assert cluster_by_label(clusters, "seo").answer_engine_candidate is True
    # A single non-question cluster is not an AEO candidate.
    assert cluster_by_label(clusters, "buy").answer_engine_candidate is False


def test_intent_markers_beat_question_form() -> None:
    assert classify_intent("seo ai agent pricing") == "transactional"
    assert classify_intent("best seo ai agent") == "commercial"
    assert classify_intent("login dashboard") == "navigational"
    assert classify_intent("what is an seo ai agent") == "informational"
    assert is_question("what is an seo ai agent") is True
    assert is_question("does it scale?") is True
    assert is_question("seo ai agent") is False


def test_similarity_links_topics_without_merging_unrelated_ones() -> None:
    assert similar(frozenset({"seo", "ai", "agent"}), frozenset({"seo", "agents", "ai"}))
    # Containment links a short head term to its long-tail expansions.
    assert similar(frozenset({"seo", "agent"}), frozenset({"seo", "agent", "enterprise", "guide"}))
    # One shared token out of five is not a topic.
    assert not similar(frozenset({"buy", "mongodb", "support"}), frozenset({"mongodb", "index", "tuning", "guide"}))


def test_cluster_label_names_the_shared_tokens() -> None:
    # "seo" and "ai" appear in both members, "agent" in one; the label takes the
    # most frequent tokens up to the cap and sorts them for a stable key.
    key, label = cluster_label(
        [frozenset({"seo", "ai", "agent"}), frozenset({"seo", "ai", "agents"})]
    )
    assert key == "agent+ai+seo"
    assert label == "agent ai seo"

    # A token carried by a minority of a larger cluster is left out of the name.
    key, label = cluster_label(
        [
            frozenset({"seo", "agent"}),
            frozenset({"seo", "agent"}),
            frozenset({"seo", "agent"}),
            frozenset({"seo", "agent", "enterprise"}),
        ]
    )
    assert key == "agent+seo"


def test_opportunity_score_is_bounded_and_rewards_reachable_demand() -> None:
    # Page-two demand that earns almost nothing is the highest-value case.
    reachable = opportunity_score(impressions=800, average_position=12.0, ctr=0.001, competing_pages=1)
    # Already ranked and already converting: little headroom left.
    entrenched = opportunity_score(impressions=800, average_position=1.5, ctr=0.30, competing_pages=1)
    # Real demand, but not reachable by an on-page change.
    unreachable = opportunity_score(impressions=800, average_position=70.0, ctr=0.0, competing_pages=1)
    assert 0 < unreachable < entrenched < reachable < 100
    # No demand means no opportunity, whatever the position.
    assert opportunity_score(impressions=0, average_position=3.0, ctr=0.0, competing_pages=1) == 0.0
    # An unranked cluster cannot be scored.
    assert opportunity_score(impressions=900, average_position=None, ctr=0.0, competing_pages=1) == 0.0


def test_cannibalisation_raises_a_cluster_above_its_single_page_twin() -> None:
    single = opportunity_score(impressions=2000, average_position=12.0, ctr=0.01, competing_pages=1)
    competing = opportunity_score(impressions=2000, average_position=12.0, ctr=0.01, competing_pages=3)
    assert competing > single


def test_query_envelope_is_bound_to_its_site() -> None:
    key = b"k" * 32
    ciphertext, nonce, aad_hash = seal_query(key, TENANT, SITE, "local-v1", "confidential query")
    assert b"confidential" not in ciphertext

    assert (
        open_query(key, TENANT, SITE, "local-v1", nonce, ciphertext, aad_hash)
        == "confidential query"
    )
    # A row lifted into another site's context does not open.
    with pytest.raises(ValueError, match="search_query_aad_mismatch"):
        open_query(key, TENANT, OTHER_SITE, "local-v1", nonce, ciphertext, aad_hash)
    assert query_aad(TENANT, SITE, "local-v1") != query_aad(TENANT, OTHER_SITE, "local-v1")


def test_empty_input_produces_no_clusters() -> None:
    assert build_clusters([]) == []


def test_stemming_merges_word_forms_without_mangling_short_words() -> None:
    from app.keywords.cluster import stem

    # The forms that matter for grouping reduce to one stem.
    assert stem("calculator") == stem("calculate") == stem("calculating")
    assert stem("percentage") == stem("percentages")
    assert stem("agents") == stem("agent")
    assert stem("guides") == stem("guide")
    assert stem("pricing") == stem("price")
    # Stripping stops before it would destroy a short word.
    assert stem("error") == "error"
    assert stem("tuning") == "tuning"
    assert stem("seo") == "seo"
    assert stem("ai") == "ai"


def test_verb_and_noun_phrasings_of_one_topic_cluster_together() -> None:
    clusters = build_clusters(
        [
            query("percentage calculator", 200, 9000, 7.5, PAGE_A),
            query("how to calculate percentage", 20, 3000, 13.0, PAGE_A),
            query("what is a percentage calculator", 4, 900, 16.0, PAGE_A),
        ]
    )
    assert len(clusters) == 1
    assert clusters[0].member_count == 3
    # The label uses the words searchers typed, not the stems.
    assert "calculat" not in clusters[0].label.split()


def test_labels_never_expose_a_stem() -> None:
    for cluster in build_clusters(SAMPLE):
        for token in cluster.label.split():
            assert token.isalnum()
