"""Runs a keyword analysis over a window of stored search evidence.

Terms are decrypted only inside this call, held in memory for the clustering
pass, and never written back in readable form. The run is versioned and
content-hashed so the same window and algorithm reproduce the same clusters.
"""

import hashlib
import json
import logging
from datetime import date
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag

from app.keywords.cluster import (
    ALGORITHM_VERSION,
    MAX_QUERIES_PER_RUN,
    KeywordCluster,
    QueryMetrics,
    build_clusters,
)
from app.keywords.secrets import open_query

logger = logging.getLogger(__name__)

# Queries below this demand are noise for clustering and would dominate the
# component graph with one-off long-tail terms.
MIN_IMPRESSIONS = 1.0

LOAD_QUERIES_SQL = """
WITH windowed AS (
  SELECT query_hash, page_id, clicks, impressions, position
  FROM search_metric
  WHERE tenant_id=$1 AND site_id=$2 AND metric_date BETWEEN $3 AND $4
), aggregated AS (
  SELECT query_hash,
         SUM(clicks) AS clicks,
         SUM(impressions) AS impressions,
         CASE WHEN SUM(impressions) > 0
              THEN SUM(position*impressions)/SUM(impressions)
              ELSE 0 END AS position
  FROM windowed GROUP BY query_hash
), best_page AS (
  SELECT DISTINCT ON (query_hash) query_hash, page_id
  FROM (
    SELECT query_hash, page_id, SUM(impressions) AS page_impressions
    FROM windowed WHERE page_id IS NOT NULL
    GROUP BY query_hash, page_id
  ) ranked
  ORDER BY query_hash, page_impressions DESC, page_id
)
SELECT a.query_hash, a.clicks, a.impressions, a.position, b.page_id,
       q.ciphertext, q.nonce, q.aad_hash, q.key_version
FROM aggregated a
JOIN search_query q
  ON q.tenant_id=$1 AND q.site_id=$2 AND q.query_hash=a.query_hash
LEFT JOIN best_page b ON b.query_hash=a.query_hash
WHERE a.impressions >= $5
ORDER BY a.impressions DESC, a.query_hash
LIMIT $6
"""


async def load_query_metrics(
    connection: Any,
    tenant_id: UUID,
    site_id: UUID,
    window_start: date,
    window_end: date,
    encryption_key: bytes,
) -> list[QueryMetrics]:
    rows = await connection.fetch(
        LOAD_QUERIES_SQL,
        tenant_id, site_id, window_start, window_end, MIN_IMPRESSIONS, MAX_QUERIES_PER_RUN,
    )
    metrics: list[QueryMetrics] = []
    undecryptable = 0
    for row in rows:
        try:
            term = open_query(
                encryption_key, tenant_id, site_id, row["key_version"],
                bytes(row["nonce"]), bytes(row["ciphertext"]), row["aad_hash"],
            )
        except (ValueError, InvalidTag, TypeError):
            # A key rotation can leave older rows unreadable. Skip them rather
            # than failing the run, and never log the row content.
            undecryptable += 1
            continue
        metrics.append(
            QueryMetrics(
                query_hash=row["query_hash"],
                term=term,
                clicks=float(row["clicks"]),
                impressions=float(row["impressions"]),
                position=float(row["position"]),
                best_page_id=row["page_id"],
            )
        )
    if undecryptable:
        logger.warning(
            "skipped undecryptable search queries", extra={"skipped_count": undecryptable}
        )
    return metrics


def analysis_content_hash(clusters: list[KeywordCluster]) -> str:
    """Hash the readable cluster shape, never the member terms."""
    summary = [
        {
            "cluster_key": cluster.cluster_key,
            "intent": cluster.intent,
            "answer_engine_candidate": cluster.answer_engine_candidate,
            "member_count": cluster.member_count,
            "impressions": cluster.impressions,
            "clicks": cluster.clicks,
            "opportunity_score": cluster.opportunity_score,
        }
        for cluster in clusters
    ]
    return hashlib.sha256(
        json.dumps(
            {"algorithm_version": ALGORITHM_VERSION, "clusters": summary},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def run_keyword_analysis(
    connection: Any,
    tenant_id: UUID,
    site_id: UUID,
    window_start: date,
    window_end: date,
    encryption_key: bytes,
    routine_run_id: UUID | None = None,
) -> dict[str, Any]:
    """Build and persist clusters for a window. Returns a run summary."""
    metrics = await load_query_metrics(
        connection, tenant_id, site_id, window_start, window_end, encryption_key
    )
    clusters = build_clusters(metrics)
    digest = analysis_content_hash(clusters)

    run_id = await connection.fetchval(
        """
        INSERT INTO keyword_analysis_run(
          tenant_id,site_id,routine_run_id,algorithm_version,status,
          window_start,window_end,queries_considered,clusters_built,content_hash)
        VALUES($1,$2,$3,$4,'completed',$5,$6,$7,$8,$9)
        ON CONFLICT(tenant_id,site_id,window_start,window_end,algorithm_version) DO UPDATE
          SET routine_run_id=EXCLUDED.routine_run_id,
              queries_considered=EXCLUDED.queries_considered,
              clusters_built=EXCLUDED.clusters_built,
              content_hash=EXCLUDED.content_hash,
              created_at=now()
        RETURNING id
        """,
        tenant_id, site_id, routine_run_id, ALGORITHM_VERSION,
        window_start, window_end, len(metrics), len(clusters), digest,
    )
    # A rerun of the same window replaces its clusters wholesale, so a stale
    # cluster from an earlier evidence set cannot survive.
    await connection.execute(
        """
        DELETE FROM keyword_cluster_member
        WHERE tenant_id=$1 AND cluster_id IN (
          SELECT id FROM keyword_cluster WHERE tenant_id=$1 AND analysis_run_id=$2
        )
        """,
        tenant_id, run_id,
    )
    await connection.execute(
        "DELETE FROM keyword_cluster WHERE tenant_id=$1 AND analysis_run_id=$2",
        tenant_id, run_id,
    )

    for cluster in clusters:
        cluster_id = await connection.fetchval(
            """
            INSERT INTO keyword_cluster(
              tenant_id,site_id,analysis_run_id,label,cluster_key,intent,
              answer_engine_candidate,member_count,clicks,impressions,ctr,
              best_position,average_position,striking_distance_count,
              primary_page_id,competing_page_count,opportunity_score)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
            RETURNING id
            """,
            tenant_id, site_id, run_id, cluster.label[:200], cluster.cluster_key[:200],
            cluster.intent, cluster.answer_engine_candidate, cluster.member_count,
            cluster.clicks, cluster.impressions, min(cluster.ctr, 1.0),
            cluster.best_position, cluster.average_position,
            cluster.striking_distance_count, cluster.primary_page_id,
            cluster.competing_page_count, cluster.opportunity_score,
        )
        await connection.executemany(
            """
            INSERT INTO keyword_cluster_member(
              tenant_id,cluster_id,site_id,query_hash,clicks,impressions,ctr,position,best_page_id)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            [
                (
                    tenant_id, cluster_id, site_id, member.query_hash,
                    member.clicks, member.impressions, min(member.ctr, 1.0),
                    member.position, member.best_page_id,
                )
                for member in cluster.members
            ],
        )

    return {
        "analysis_run_id": str(run_id),
        "algorithm_version": ALGORITHM_VERSION,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "queries_considered": len(metrics),
        "clusters_built": len(clusters),
        "answer_engine_clusters": sum(
            1 for cluster in clusters if cluster.answer_engine_candidate
        ),
        "content_hash": digest,
    }
