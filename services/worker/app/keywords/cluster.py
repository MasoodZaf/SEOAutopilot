"""Deterministic keyword clustering, intent mapping, and AEO candidacy.

No model call is involved: the same query set and the same algorithm version
always produce the same clusters, in the same order, with the same scores. That
is what lets a cluster be cited as evidence rather than presented as an opinion.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

ALGORITHM_VERSION = "keyword-cluster-v1"

# Tokens carrying no topical signal. Kept small and explicit on purpose: an
# aggressive list silently reshapes clusters and is hard to audit.
STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "into", "is", "it", "of", "on", "or", "that", "the", "to", "with", "what", "when", "where", "which", "who", "whom", "why", "do", "does", "did", "can", "could", "should", "would", "will", "my", "your", "our", "their", "his", "her", "its", "me", "you", "we", "they", "i"]
)

QUESTION_PREFIXES = frozenset(
    ["how", "what", "why", "who", "when", "where", "which", "can", "could", "does", "do", "is", "are", "should", "will"]
)

# Intent markers. First match in precedence order wins for a single query.
TRANSACTIONAL_MARKERS = frozenset(
    ["buy", "price", "pricing", "cost", "costs", "cheap", "cheapest", "discount", "coupon", "deal", "deals", "order", "purchase", "subscribe", "subscription", "hire", "quote", "trial", "signup", "plans", "plan"]
)
COMMERCIAL_MARKERS = frozenset(
    ["best", "top", "review", "reviews", "vs", "versus", "alternative", "alternatives", "comparison", "compare", "rated", "ranking", "recommended"]
)
NAVIGATIONAL_MARKERS = frozenset(
    ["login", "log-in", "signin", "dashboard", "download", "docs", "documentation", "contact", "careers", "support", "portal", "account"]
)
INFORMATIONAL_MARKERS = frozenset(
    ["how", "what", "why", "guide", "tutorial", "tutorials", "example", "examples", "tips", "ideas", "meaning", "definition", "explained", "learn", "basics", "introduction"]
)

# Precedence used only to break a tie deterministically.
INTENT_PRECEDENCE = ("transactional", "commercial", "navigational", "informational")

# Two queries join the same cluster when their significant tokens overlap at
# this Jaccard ratio, or when one is a subset of the other. 0.34 keeps
# "seo ai agent" and "how do seo agents work" together while leaving
# "buy mongodb support" out of a mongodb tuning cluster.
SIMILARITY_THRESHOLD = 0.34
SUBSET_MIN_TOKENS = 2

# Suffixes stripped, in order, when comparing tokens. Longer forms come first so
# "guides" reduces through "es" rather than "s".
STEM_SUFFIXES = ("ing", "ed", "es", "s", "or", "er", "e")
MIN_STEM_LENGTH = 4
MAX_STEM_PASSES = 6

# Tokens naming at least this share of a cluster's members become its label.
LABEL_SHARE = 0.5
LABEL_MAX_TOKENS = 3
OTHER_CLUSTER_KEY = "__unclustered__"

# Pairwise comparison is bounded by an inverted index, but a runaway query set
# is still capped so one site cannot monopolise a routine slot.
MAX_QUERIES_PER_RUN = 20_000

STRIKING_DISTANCE_MIN = 10.1
STRIKING_DISTANCE_MAX = 20.0

# Impressions at which demand is treated as saturated for scoring.
DEMAND_SATURATION_IMPRESSIONS = 10_000.0

# Position bands for reachable headroom. A page already in the top three has
# little room; a page past 40 is not reachable by an on-page change alone.
POSITION_HEADROOM = (
    (0.0, 4.0, 0.40),
    (4.0, 8.0, 0.70),
    (8.0, 20.0, 1.00),
    (20.0, 40.0, 0.50),
)
POSITION_HEADROOM_FLOOR = 0.20

# Rough organic CTR by rounded position. A heuristic reference curve used only
# to flag an underperforming cluster, never reported as an expected outcome.
REFERENCE_CTR = {
    1: 0.28, 2: 0.15, 3: 0.10, 4: 0.07, 5: 0.05,
    6: 0.04, 7: 0.03, 8: 0.026, 9: 0.022, 10: 0.02,
}
REFERENCE_CTR_TAIL = 0.01


@dataclass(frozen=True, slots=True)
class QueryMetrics:
    query_hash: str
    term: str
    clicks: float
    impressions: float
    position: float
    best_page_id: UUID | None = None
    competing_page_count: int = 0


@dataclass(frozen=True, slots=True)
class ClusterMember:
    query_hash: str
    clicks: float
    impressions: float
    ctr: float
    position: float
    best_page_id: UUID | None


@dataclass(frozen=True, slots=True)
class KeywordCluster:
    cluster_key: str
    label: str
    intent: str
    answer_engine_candidate: bool
    clicks: float
    impressions: float
    ctr: float
    best_position: float | None
    average_position: float | None
    striking_distance_count: int
    primary_page_id: UUID | None
    competing_page_count: int
    opportunity_score: float
    members: list[ClusterMember] = field(default_factory=list)

    @property
    def member_count(self) -> int:
        return len(self.members)


def tokenize(term: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", term.lower()) if len(token) >= 2]


def significant_tokens(term: str) -> list[str]:
    return [token for token in tokenize(term) if token not in STOPWORDS]


def stem(token: str) -> str:
    """Conservative suffix stripping used only to compare tokens.

    "calculator", "calculate" and "calculating" all reduce to "calculat", which
    is what keeps those phrasings in one cluster. Stripping stops whenever the
    remaining stem would fall below MIN_STEM_LENGTH, so short words such as
    "error", "tuning" and "seo" survive intact rather than being mangled.

    Labels are built from the original tokens, so this never reaches the reader.
    """
    current = token
    for _ in range(MAX_STEM_PASSES):
        for suffix in STEM_SUFFIXES:
            if current.endswith(suffix) and len(current) - len(suffix) >= MIN_STEM_LENGTH:
                current = current[: -len(suffix)]
                break
        else:
            return current
    return current


def similarity_tokens(term: str) -> frozenset[str]:
    return frozenset(stem(token) for token in significant_tokens(term))


def is_question(term: str) -> bool:
    """A question-form query is the observable AEO signal available to us."""
    stripped = term.strip().lower()
    if stripped.endswith("?"):
        return True
    tokens = tokenize(stripped)
    return bool(tokens) and tokens[0] in QUESTION_PREFIXES


def classify_intent(term: str) -> str:
    tokens = set(tokenize(term))
    if tokens & TRANSACTIONAL_MARKERS:
        return "transactional"
    if tokens & COMMERCIAL_MARKERS:
        return "commercial"
    if tokens & NAVIGATIONAL_MARKERS:
        return "navigational"
    if tokens & INFORMATIONAL_MARKERS or is_question(term):
        return "informational"
    return "informational"


class _UnionFind:
    """Union-find over query indexes, used to grow clusters transitively."""

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def similar(left: frozenset[str], right: frozenset[str]) -> bool:
    """Two token sets belong together by overlap ratio or containment."""
    if not left or not right:
        return False
    if len(left) >= SUBSET_MIN_TOKENS and left <= right:
        return True
    if len(right) >= SUBSET_MIN_TOKENS and right <= left:
        return True
    overlap = len(left & right)
    if overlap == 0:
        return False
    return overlap / len(left | right) >= SIMILARITY_THRESHOLD


def cluster_label(groups: list[frozenset[str]]) -> tuple[str, str]:
    """Name a cluster by the tokens most of its members share."""
    if not groups:
        return OTHER_CLUSTER_KEY, "unclustered"
    frequency: dict[str, int] = defaultdict(int)
    for tokens in groups:
        for token in tokens:
            frequency[token] += 1
    threshold = max(1, math.ceil(len(groups) * LABEL_SHARE))
    shared = [token for token, count in frequency.items() if count >= threshold]
    if not shared:
        shared = list(frequency)
    ordered = sorted(shared, key=lambda token: (-frequency[token], token))[:LABEL_MAX_TOKENS]
    if not ordered:
        return OTHER_CLUSTER_KEY, "unclustered"
    signature = sorted(ordered)
    return "+".join(signature), " ".join(signature)


def reference_ctr(position: float) -> float:
    return REFERENCE_CTR.get(max(1, round(position)), REFERENCE_CTR_TAIL)


def position_headroom(position: float) -> float:
    for lower, upper, weight in POSITION_HEADROOM:
        if lower <= position < upper:
            return weight
    return POSITION_HEADROOM_FLOOR


def opportunity_score(
    impressions: float, average_position: float | None, ctr: float, competing_pages: int
) -> float:
    """Bounded, explainable demand x headroom x underperformance score."""
    if impressions <= 0 or average_position is None:
        return 0.0
    demand = min(1.0, math.log10(1.0 + impressions) / math.log10(1.0 + DEMAND_SATURATION_IMPRESSIONS))
    headroom = position_headroom(average_position)
    expected = reference_ctr(average_position)
    # 1.0 when the cluster already meets the reference curve, up to 1.4 when it
    # earns nothing near a position that should convert.
    shortfall = max(0.0, (expected - ctr) / expected) if expected > 0 else 0.0
    underperformance = 1.0 + 0.4 * min(1.0, shortfall)
    # More than one page ranking for the same cluster is a consolidation signal.
    cannibalization = 1.1 if competing_pages > 1 else 1.0
    raw = demand * headroom * underperformance * cannibalization
    return round(min(100.0, raw * 100), 2)


def build_clusters(queries: list[QueryMetrics]) -> list[KeywordCluster]:
    """Group queries into deterministic, ranked topic clusters.

    Queries are linked pairwise on token overlap and the clusters are the
    connected components of that graph, so a topic stays whole even when no
    single phrasing is shared by all of its members. Sorting the input first
    makes component membership independent of database row order.
    """
    if not queries:
        return []
    ordered_queries = sorted(queries, key=lambda item: item.query_hash)[:MAX_QUERIES_PER_RUN]
    # The graph compares stemmed tokens; the label is built from the words the
    # searcher actually used.
    token_sets = [similarity_tokens(item.term) for item in ordered_queries]
    label_sets = [frozenset(significant_tokens(item.term)) for item in ordered_queries]

    # Only queries sharing at least one token can possibly be linked, so the
    # inverted index keeps this far below a full pairwise scan.
    postings: dict[str, list[int]] = defaultdict(list)
    for index, tokens in enumerate(token_sets):
        for token in tokens:
            postings[token].append(index)

    groups = _UnionFind(len(ordered_queries))
    for index, tokens in enumerate(token_sets):
        candidates: set[int] = set()
        for token in tokens:
            candidates.update(postings[token])
        for other in candidates:
            if other > index and similar(tokens, token_sets[other]):
                groups.union(index, other)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(ordered_queries)):
        components[groups.find(index)].append(index)

    clusters: list[KeywordCluster] = []
    used_keys: dict[str, int] = defaultdict(int)
    for root in sorted(components):
        indexes = components[root]
        group = sorted(
            (ordered_queries[index] for index in indexes),
            key=lambda item: (-item.impressions, item.query_hash),
        )
        key, label = cluster_label([label_sets[index] for index in indexes if label_sets[index]])
        used_keys[key] += 1
        if used_keys[key] > 1:
            # Two distinct components can share a label; keep the key unique
            # per run without changing what the reader sees.
            key = f"{key}#{used_keys[key]}"

        clicks = sum(item.clicks for item in group)
        impressions = sum(item.impressions for item in group)
        ctr = round(clicks / impressions, 6) if impressions > 0 else 0.0

        ranked = [item for item in group if item.position > 0]
        best_position = min((item.position for item in ranked), default=None)
        weight = sum(item.impressions for item in ranked)
        average_position = (
            round(sum(item.position * item.impressions for item in ranked) / weight, 4)
            if ranked and weight > 0
            else None
        )

        intent_weight: dict[str, float] = defaultdict(float)
        questions = 0
        for item in group:
            intent_weight[classify_intent(item.term)] += max(item.impressions, 1.0)
            questions += int(is_question(item.term))
        intent = max(
            intent_weight,
            key=lambda name: (intent_weight[name], -INTENT_PRECEDENCE.index(name)),
        )

        page_weight: dict[UUID, float] = defaultdict(float)
        for item in group:
            if item.best_page_id is not None:
                page_weight[item.best_page_id] += item.impressions
        primary_page_id = (
            max(page_weight, key=lambda page: (page_weight[page], str(page)))
            if page_weight
            else None
        )

        striking = sum(
            1
            for item in group
            if STRIKING_DISTANCE_MIN <= item.position <= STRIKING_DISTANCE_MAX
        )
        clusters.append(
            KeywordCluster(
                cluster_key=key,
                label=label,
                intent=intent,
                # A cluster is an answer-engine candidate when questions are a
                # real share of it, not merely present once in a long tail.
                answer_engine_candidate=questions > 0 and questions / len(group) >= 0.30,
                clicks=round(clicks, 4),
                impressions=round(impressions, 4),
                ctr=ctr,
                best_position=round(best_position, 4) if best_position is not None else None,
                average_position=average_position,
                striking_distance_count=striking,
                primary_page_id=primary_page_id,
                competing_page_count=len(page_weight),
                opportunity_score=opportunity_score(
                    impressions, average_position, ctr, len(page_weight)
                ),
                members=[
                    ClusterMember(
                        query_hash=item.query_hash,
                        clicks=round(item.clicks, 4),
                        impressions=round(item.impressions, 4),
                        ctr=round(item.clicks / item.impressions, 6)
                        if item.impressions > 0
                        else 0.0,
                        position=round(item.position, 4),
                        best_page_id=item.best_page_id,
                    )
                    for item in group
                ],
            )
        )
    clusters.sort(key=lambda cluster: (-cluster.opportunity_score, cluster.cluster_key))
    return clusters
