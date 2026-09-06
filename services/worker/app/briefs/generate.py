"""Generates and persists content briefs for a site's latest keyword analysis.

Briefs are regenerated per analysis run and keyed on the cluster, so a rerun
updates a brief in place instead of accumulating duplicates. A brief a human has
acted on or dismissed keeps its status; only its evidence is refreshed.
"""

import json
import logging
from typing import Any
from uuid import UUID

from app.briefs.build import ClusterInput, PageInput, build_brief

logger = logging.getLogger(__name__)

# Only clusters with real demand earn a brief; the long tail would otherwise
# generate more work than a team can read.
MIN_CLUSTER_SCORE = 20.0
MAX_BRIEFS_PER_RUN = 50

# The bar above is absolute, and the score it reads is dominated by absolute
# demand. On a site that has not started ranking, no cluster can ever clear it:
# the pilot site's strongest topic scored 12.5 from 41 impressions at position
# 86, so the one mechanism that says "here is what to add to this page"
# produced nothing for exactly the site that needed it.
#
# When nothing clears the bar, the site's own strongest topics are offered
# instead -- a short list, above a floor that keeps single stray impressions
# out. They are recorded as `exploratory` so nobody mistakes "the best this
# site has" for "worth a team's week".
EXPLORATORY_FLOOR = 3.0
MAX_EXPLORATORY_BRIEFS = 5

LOAD_CLUSTERS_SQL = """
SELECT c.id, c.analysis_run_id, c.label, c.intent, c.answer_engine_candidate,
       c.impressions, c.clicks, c.ctr, c.average_position,
       c.striking_distance_count, c.competing_page_count, c.opportunity_score,
       c.member_count, c.primary_page_id,
       ARRAY(
         SELECT m.query_hash FROM keyword_cluster_member m
         WHERE m.tenant_id=c.tenant_id AND m.cluster_id=c.id
         ORDER BY m.impressions DESC, m.query_hash
       ) AS query_hashes
FROM keyword_cluster c
WHERE c.tenant_id=$1 AND c.site_id=$2 AND c.analysis_run_id=$3
  AND c.opportunity_score >= $4
ORDER BY c.opportunity_score DESC, c.cluster_key
LIMIT $5
"""

# Latest observation per page, plus its inbound internal link count.
LOAD_PAGE_SQL = """
SELECT p.id, p.normalized_url, o.title, o.meta_description, o.h1_json,
       o.word_count, o.structured_data_json, o.http_status, o.canonical_url,
       o.robots_directives,
       (SELECT count(*) FROM link_edge e
        WHERE e.tenant_id=p.tenant_id AND e.target_url_hash=p.url_hash) AS inbound_links
FROM page p
JOIN LATERAL (
  SELECT * FROM page_observation po
  WHERE po.tenant_id=p.tenant_id AND po.page_id=p.id
  ORDER BY po.observed_at DESC LIMIT 1
) o ON true
WHERE p.tenant_id=$1 AND p.id=$2
"""


def structured_data_types(raw: Any) -> list[str]:
    """Pull @type values out of stored JSON-LD without trusting its shape."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    types: list[str] = []
    def visit(value: Any, depth: int = 0) -> None:
        if depth > 6 or len(types) >= 40:
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            declared = value.get("@type")
            if isinstance(declared, str):
                types.append(declared)
            elif isinstance(declared, list):
                types.extend(item for item in declared if isinstance(item, str))
            for item in value.values():
                visit(item, depth + 1)
    visit(raw)
    return sorted(set(types))


async def _load_page(connection: Any, tenant_id: UUID, page_id: UUID) -> PageInput | None:
    row = await connection.fetchrow(LOAD_PAGE_SQL, tenant_id, page_id)
    if row is None:
        return None
    headings = row["h1_json"]
    if isinstance(headings, str):
        headings = json.loads(headings)
    return PageInput(
        page_id=row["id"],
        normalized_url=row["normalized_url"],
        title=row["title"],
        meta_description=row["meta_description"],
        h1=[item for item in (headings or []) if isinstance(item, str)],
        word_count=int(row["word_count"] or 0),
        structured_data_types=structured_data_types(row["structured_data_json"]),
        inbound_internal_links=int(row["inbound_links"] or 0),
        http_status=row["http_status"],
        canonical_url=row["canonical_url"],
        robots_directives=list(row["robots_directives"] or []),
    )


async def generate_briefs(
    connection: Any,
    tenant_id: UUID,
    site_id: UUID,
    analysis_run_id: UUID,
    routine_run_id: UUID | None = None,
) -> dict[str, Any]:
    clusters = await connection.fetch(
        LOAD_CLUSTERS_SQL, tenant_id, site_id, analysis_run_id,
        MIN_CLUSTER_SCORE, MAX_BRIEFS_PER_RUN,
    )
    selection_basis = "demand"
    if not clusters:
        clusters = await connection.fetch(
            LOAD_CLUSTERS_SQL, tenant_id, site_id, analysis_run_id,
            EXPLORATORY_FLOOR, MAX_EXPLORATORY_BRIEFS,
        )
        selection_basis = "exploratory"
    written = 0
    refreshes = 0
    new_pages = 0
    for row in clusters:
        page = (
            await _load_page(connection, tenant_id, row["primary_page_id"])
            if row["primary_page_id"] is not None
            else None
        )
        brief = build_brief(
            ClusterInput(
                cluster_id=row["id"],
                analysis_run_id=row["analysis_run_id"],
                label=row["label"],
                intent=row["intent"],
                answer_engine_candidate=row["answer_engine_candidate"],
                impressions=float(row["impressions"]),
                clicks=float(row["clicks"]),
                ctr=float(row["ctr"]),
                average_position=(
                    float(row["average_position"])
                    if row["average_position"] is not None
                    else None
                ),
                striking_distance_count=int(row["striking_distance_count"]),
                competing_page_count=int(row["competing_page_count"]),
                opportunity_score=float(row["opportunity_score"]),
                member_count=int(row["member_count"]),
                query_hashes=list(row["query_hashes"] or []),
            ),
            page,
        )
        await connection.execute(
            """
            INSERT INTO content_brief(
              tenant_id,site_id,keyword_cluster_id,analysis_run_id,routine_run_id,
              kind,target_page_id,cluster_label,intent,answer_engine_candidate,
              priority_score,sections_json,evidence_json,query_hashes,content_hash)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14,$15)
            ON CONFLICT(tenant_id,keyword_cluster_id) DO UPDATE SET
              analysis_run_id=EXCLUDED.analysis_run_id,
              routine_run_id=EXCLUDED.routine_run_id,
              kind=EXCLUDED.kind,
              target_page_id=EXCLUDED.target_page_id,
              cluster_label=EXCLUDED.cluster_label,
              intent=EXCLUDED.intent,
              answer_engine_candidate=EXCLUDED.answer_engine_candidate,
              priority_score=EXCLUDED.priority_score,
              sections_json=EXCLUDED.sections_json,
              evidence_json=EXCLUDED.evidence_json,
              query_hashes=EXCLUDED.query_hashes,
              content_hash=EXCLUDED.content_hash,
              version=content_brief.version+1,
              updated_at=now()
            """,
            tenant_id, site_id, brief.cluster_id, brief.analysis_run_id, routine_run_id,
            brief.kind, brief.target_page_id, brief.cluster_label[:200], brief.intent,
            brief.answer_engine_candidate, brief.priority_score,
            json.dumps(
                [
                    {
                        "key": section.key,
                        "title": section.title,
                        "finding": section.finding,
                        "recommendation": section.recommendation,
                        "evidence": section.evidence,
                    }
                    for section in brief.sections
                ],
                sort_keys=True, separators=(",", ":"), default=str,
            ),
            # The basis travels with the brief, not just with the run that made
            # it: a reader opening one months later must be able to tell
            # whether it was chosen on its merits or for lack of anything
            # stronger.
            json.dumps(
                {**brief.evidence, "selection_basis": selection_basis},
                sort_keys=True, separators=(",", ":"), default=str,
            ),
            brief.query_hashes,
            brief.content_hash,
        )
        written += 1
        if brief.kind == "refresh":
            refreshes += 1
        else:
            new_pages += 1

    return {
        "analysis_run_id": str(analysis_run_id),
        "clusters_considered": len(clusters),
        "briefs_written": written,
        "refresh_briefs": refreshes,
        "new_page_briefs": new_pages,
        "minimum_cluster_score": MIN_CLUSTER_SCORE,
        "selection_basis": selection_basis,
    }
