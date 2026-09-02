"""Deterministic content briefs from a keyword cluster and page evidence.

A brief is advisory. It states what the evidence shows, what to change, and
which observation each statement came from. No model writes it, so the same
cluster and the same page observation always produce the same brief and the
same content hash.

Nothing here handles a readable query term: the cluster label already carries
the shared tokens, and members are referenced by hash so the brief can be
stored and shown without widening who can read what people searched for.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

BRIEF_VERSION = "content-brief-v1"

TITLE_MIN, TITLE_MAX = 15, 65
DESCRIPTION_MIN, DESCRIPTION_MAX = 50, 170

# Below this a page is too thin to satisfy a researched query; above it the
# brief stops asking for length and asks for coverage instead.
THIN_CONTENT_WORDS = 300
# Pages with fewer inbound internal links than this are hard to discover.
WEAK_INTERNAL_LINKING = 3

# Structured data types that make a page eligible for answer-engine surfaces.
ANSWER_ENGINE_TYPES = frozenset({"FAQPage", "QAPage", "HowTo"})

# Each unmet gap lifts the cluster's own score by this much, capped at 100.
GAP_WEIGHT = 0.06


@dataclass(frozen=True, slots=True)
class ClusterInput:
    cluster_id: UUID
    analysis_run_id: UUID
    label: str
    intent: str
    answer_engine_candidate: bool
    impressions: float
    clicks: float
    ctr: float
    average_position: float | None
    striking_distance_count: int
    competing_page_count: int
    opportunity_score: float
    member_count: int
    query_hashes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PageInput:
    page_id: UUID
    normalized_url: str
    title: str | None
    meta_description: str | None
    h1: list[str]
    word_count: int
    structured_data_types: list[str]
    inbound_internal_links: int
    http_status: int | None
    canonical_url: str | None
    robots_directives: list[str]


@dataclass(frozen=True, slots=True)
class BriefSection:
    key: str
    title: str
    finding: str
    recommendation: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ContentBrief:
    kind: str
    cluster_id: UUID
    analysis_run_id: UUID
    target_page_id: UUID | None
    cluster_label: str
    intent: str
    answer_engine_candidate: bool
    priority_score: float
    sections: list[BriefSection]
    evidence: dict[str, Any]
    query_hashes: list[str]
    content_hash: str


def label_tokens(label: str) -> list[str]:
    return [token for token in label.split() if token]


def _covers(text: str | None, tokens: list[str]) -> list[str]:
    """Return the cluster tokens missing from a piece of page text."""
    if not tokens:
        return []
    lowered = (text or "").lower()
    return [token for token in tokens if token not in lowered]


def build_content_hash(sections: list[BriefSection], evidence: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "version": BRIEF_VERSION,
                "sections": [
                    {
                        "key": section.key,
                        "finding": section.finding,
                        "recommendation": section.recommendation,
                    }
                    for section in sections
                ],
                "evidence": evidence,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _new_page_sections(cluster: ClusterInput) -> list[BriefSection]:
    tokens = label_tokens(cluster.label)
    sections = [
        BriefSection(
            key="target",
            title="Target and intent",
            finding=(
                f"{cluster.member_count} queries in this cluster drew "
                f"{cluster.impressions:.0f} impressions but no page on this site ranks for them."
            ),
            recommendation=(
                f"Create a page targeting '{cluster.label}' written for {cluster.intent} intent."
            ),
            evidence={
                "cluster_label": cluster.label,
                "intent": cluster.intent,
                "impressions": cluster.impressions,
                "member_count": cluster.member_count,
            },
        ),
        BriefSection(
            key="on_page",
            title="On-page essentials",
            finding="A new page starts with no title, description, or heading evidence.",
            recommendation=(
                f"Write a {TITLE_MIN}-{TITLE_MAX} character title and a "
                f"{DESCRIPTION_MIN}-{DESCRIPTION_MAX} character description that both use "
                f"{', '.join(tokens) or 'the cluster terms'}, and give the page a single H1."
            ),
            evidence={"required_tokens": tokens},
        ),
    ]
    if cluster.answer_engine_candidate:
        sections.append(
            BriefSection(
                key="answer_engine",
                title="Answer-engine coverage",
                finding=(
                    "At least 30% of this cluster's queries are question-form, so the topic is "
                    "read by answer engines as a question."
                ),
                recommendation=(
                    "Answer each question directly in its own section and publish matching "
                    "FAQPage or QAPage JSON-LD."
                ),
                evidence={"answer_engine_candidate": True},
            )
        )
    return sections


def _refresh_sections(cluster: ClusterInput, page: PageInput) -> list[BriefSection]:
    tokens = label_tokens(cluster.label)
    sections: list[BriefSection] = []

    position = cluster.average_position
    if position is not None and cluster.striking_distance_count > 0:
        finding = (
            f"{cluster.striking_distance_count} of {cluster.member_count} queries sit in "
            f"positions 11-20, averaging position {position:.1f}."
        )
    elif position is not None:
        finding = f"The cluster averages position {position:.1f} across {cluster.member_count} queries."
    else:
        finding = f"{cluster.member_count} queries are attributed to this page without a ranking position."
    sections.append(
        BriefSection(
            key="target",
            title="Target and intent",
            finding=finding,
            recommendation=(
                f"Refresh this page for '{cluster.label}' with {cluster.intent} intent; "
                f"keep the existing URL."
            ),
            evidence={
                "cluster_label": cluster.label,
                "intent": cluster.intent,
                "impressions": cluster.impressions,
                "clicks": cluster.clicks,
                "ctr": cluster.ctr,
                "average_position": position,
                "striking_distance_count": cluster.striking_distance_count,
            },
        )
    )

    title = (page.title or "").strip()
    missing_in_title = _covers(title, tokens)
    if not title:
        sections.append(
            BriefSection(
                key="title", title="Title",
                finding="The page has no title element.",
                recommendation=(
                    f"Add a {TITLE_MIN}-{TITLE_MAX} character title leading with "
                    f"{', '.join(tokens) or 'the cluster terms'}."
                ),
                evidence={"current_title": None},
            )
        )
    elif missing_in_title or not TITLE_MIN <= len(title) <= TITLE_MAX:
        sections.append(
            BriefSection(
                key="title", title="Title",
                finding=(
                    f"The title is {len(title)} characters"
                    + (f" and omits {', '.join(missing_in_title)}." if missing_in_title else ".")
                ),
                recommendation=(
                    f"Rewrite the title to {TITLE_MIN}-{TITLE_MAX} characters covering "
                    f"{', '.join(tokens)}."
                ),
                evidence={
                    "current_title": title,
                    "current_length": len(title),
                    "missing_tokens": missing_in_title,
                },
            )
        )

    description = (page.meta_description or "").strip()
    reference_ctr_gap = cluster.ctr < 0.02 and position is not None and position <= 10
    if not description or not DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX or reference_ctr_gap:
        sections.append(
            BriefSection(
                key="description", title="Meta description",
                finding=(
                    "The page has no meta description."
                    if not description
                    else f"The description is {len(description)} characters and the cluster earns "
                    f"a {cluster.ctr:.2%} click-through rate."
                ),
                recommendation=(
                    f"Write a {DESCRIPTION_MIN}-{DESCRIPTION_MAX} character description that states "
                    "the page's specific answer rather than restating the title."
                ),
                evidence={
                    "current_length": len(description),
                    "cluster_ctr": cluster.ctr,
                    "average_position": position,
                },
            )
        )

    heading = page.h1[0] if page.h1 else None
    missing_in_h1 = _covers(heading, tokens)
    if heading is None or len(page.h1) > 1 or missing_in_h1:
        sections.append(
            BriefSection(
                key="heading", title="H1 and structure",
                finding=(
                    "The page has no H1."
                    if heading is None
                    else f"The page has {len(page.h1)} H1 headings."
                    if len(page.h1) > 1
                    else f"The H1 omits {', '.join(missing_in_h1)}."
                ),
                recommendation=(
                    f"Use one H1 naming the topic as '{cluster.label}', then subheads for each "
                    "distinct question in the cluster."
                ),
                evidence={"h1": page.h1, "missing_tokens": missing_in_h1},
            )
        )

    if page.word_count < THIN_CONTENT_WORDS:
        sections.append(
            BriefSection(
                key="depth", title="Depth",
                finding=f"The page has {page.word_count} visible words.",
                recommendation=(
                    f"Expand past {THIN_CONTENT_WORDS} words by covering each query in the "
                    "cluster explicitly rather than padding the existing sections."
                ),
                evidence={"word_count": page.word_count, "threshold": THIN_CONTENT_WORDS},
            )
        )

    if cluster.answer_engine_candidate:
        present = set(page.structured_data_types) & ANSWER_ENGINE_TYPES
        if not present:
            sections.append(
                BriefSection(
                    key="answer_engine", title="Answer-engine coverage",
                    finding=(
                        "At least 30% of this cluster's queries are question-form, and the page "
                        "publishes no FAQPage, QAPage, or HowTo structured data."
                    ),
                    recommendation=(
                        "Add a question-and-answer section and matching JSON-LD so the answer is "
                        "citable rather than inferred."
                    ),
                    evidence={
                        "structured_data_types": page.structured_data_types,
                        "eligible_types": sorted(ANSWER_ENGINE_TYPES),
                    },
                )
            )

    if page.inbound_internal_links < WEAK_INTERNAL_LINKING:
        sections.append(
            BriefSection(
                key="internal_links", title="Internal linking",
                finding=f"The page has {page.inbound_internal_links} inbound internal links.",
                recommendation=(
                    f"Add at least {WEAK_INTERNAL_LINKING} inbound links from related pages, "
                    f"using anchor text drawn from '{cluster.label}'."
                ),
                evidence={
                    "inbound_internal_links": page.inbound_internal_links,
                    "threshold": WEAK_INTERNAL_LINKING,
                },
            )
        )

    if cluster.competing_page_count > 1:
        sections.append(
            BriefSection(
                key="consolidation", title="Consolidation",
                finding=(
                    f"{cluster.competing_page_count} pages rank for this cluster, which splits its "
                    "authority."
                ),
                recommendation=(
                    "Decide which page owns the topic, then consolidate or differentiate the "
                    "others. Any canonical or redirect change is a separate reviewed proposal."
                ),
                evidence={"competing_page_count": cluster.competing_page_count},
            )
        )

    return sections


def build_brief(cluster: ClusterInput, page: PageInput | None) -> ContentBrief:
    """Build one brief. `page` is None when the cluster has no ranking page."""
    kind = "refresh" if page is not None else "new_page"
    sections = (
        _refresh_sections(cluster, page) if page is not None else _new_page_sections(cluster)
    )
    # The target section is context, not a gap; gaps are what raises priority.
    gaps = max(0, len(sections) - 1)
    priority = round(min(100.0, cluster.opportunity_score * (1 + GAP_WEIGHT * gaps)), 2)

    evidence: dict[str, Any] = {
        "brief_version": BRIEF_VERSION,
        "cluster": {
            "id": str(cluster.cluster_id),
            "label": cluster.label,
            "intent": cluster.intent,
            "impressions": cluster.impressions,
            "clicks": cluster.clicks,
            "ctr": cluster.ctr,
            "average_position": cluster.average_position,
            "member_count": cluster.member_count,
            "opportunity_score": cluster.opportunity_score,
        },
        "gap_count": gaps,
    }
    if page is not None:
        evidence["page"] = {
            "id": str(page.page_id),
            "url": page.normalized_url,
            "http_status": page.http_status,
            "word_count": page.word_count,
            "inbound_internal_links": page.inbound_internal_links,
            "structured_data_types": page.structured_data_types,
        }

    return ContentBrief(
        kind=kind,
        cluster_id=cluster.cluster_id,
        analysis_run_id=cluster.analysis_run_id,
        target_page_id=page.page_id if page is not None else None,
        cluster_label=cluster.label,
        intent=cluster.intent,
        answer_engine_candidate=cluster.answer_engine_candidate,
        priority_score=priority,
        sections=sections,
        evidence=evidence,
        query_hashes=sorted(cluster.query_hashes),
        content_hash=build_content_hash(sections, evidence),
    )
