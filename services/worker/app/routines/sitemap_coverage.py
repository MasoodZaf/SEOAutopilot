"""Sitemap coverage against the crawl that produced it.

Coverage compares three sets for one crawl: what the sitemaps declared, what the
crawl reached, and what is actually indexable. Every number traces back to a
stored observation, so a gap can be opened and inspected rather than trusted.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

COVERAGE_SCHEMA_VERSION = 1
SAMPLE_LIMIT = 25


def content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


# A page counts as indexable when the crawl saw a 200, no noindex directive, and
# a canonical that either is absent or points at the page itself.
INDEXABLE_PREDICATE = """
  o.http_status = 200
  AND NOT (o.robots_directives @> ARRAY['noindex'])
  AND (o.canonical_url IS NULL OR o.canonical_url = p.normalized_url)
"""

DECLARED_SUMMARY_SQL = f"""
WITH declared AS (
  SELECT su.url_hash, su.normalized_url
  FROM sitemap_url su
  WHERE su.tenant_id=$1 AND su.crawl_job_id=$2
), reached AS (
  SELECT p.url_hash, p.normalized_url, o.http_status,
         ({INDEXABLE_PREDICATE}) AS indexable
  FROM page_observation o
  JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
  WHERE o.tenant_id=$1 AND o.crawl_job_id=$2
)
SELECT
  (SELECT count(*) FROM declared) AS declared_total,
  (SELECT count(*) FROM declared d JOIN reached r ON r.url_hash=d.url_hash) AS declared_crawled,
  (SELECT count(*) FROM declared d JOIN reached r ON r.url_hash=d.url_hash
     WHERE r.indexable) AS declared_indexable,
  (SELECT count(*) FROM reached) AS crawled_total,
  (SELECT count(*) FROM reached WHERE indexable) AS crawled_indexable
"""

DECLARED_NOT_CRAWLED_SQL = """
SELECT su.normalized_url
FROM sitemap_url su
WHERE su.tenant_id=$1 AND su.crawl_job_id=$2
  AND NOT EXISTS (
    SELECT 1 FROM page_observation o
    JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
    WHERE o.tenant_id=$1 AND o.crawl_job_id=$2 AND p.url_hash=su.url_hash
  )
ORDER BY su.normalized_url
LIMIT $3
"""

DECLARED_NOT_INDEXABLE_SQL = f"""
SELECT p.normalized_url, o.http_status,
       (o.robots_directives @> ARRAY['noindex']) AS noindex,
       o.canonical_url
FROM sitemap_url su
JOIN page p ON p.tenant_id=su.tenant_id AND p.url_hash=su.url_hash AND p.site_id=su.site_id
JOIN page_observation o ON o.page_id=p.id AND o.tenant_id=p.tenant_id AND o.crawl_job_id=$2
WHERE su.tenant_id=$1 AND su.crawl_job_id=$2 AND NOT ({INDEXABLE_PREDICATE})
ORDER BY p.normalized_url
LIMIT $3
"""

CRAWLED_NOT_DECLARED_SQL = f"""
SELECT p.normalized_url
FROM page_observation o
JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
WHERE o.tenant_id=$1 AND o.crawl_job_id=$2 AND ({INDEXABLE_PREDICATE})
  AND NOT EXISTS (
    SELECT 1 FROM sitemap_url su
    WHERE su.tenant_id=$1 AND su.crawl_job_id=$2 AND su.url_hash=p.url_hash
  )
ORDER BY p.normalized_url
LIMIT $3
"""

CRAWLED_NOT_DECLARED_COUNT_SQL = f"""
SELECT count(*)
FROM page_observation o
JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
WHERE o.tenant_id=$1 AND o.crawl_job_id=$2 AND ({INDEXABLE_PREDICATE})
  AND NOT EXISTS (
    SELECT 1 FROM sitemap_url su
    WHERE su.tenant_id=$1 AND su.crawl_job_id=$2 AND su.url_hash=p.url_hash
  )
"""


def coverage_ratio(covered: int, total: int) -> float | None:
    return round(covered / total, 4) if total > 0 else None


async def build_sitemap_coverage(
    connection: Any, tenant_id: UUID, site_id: UUID, crawl_job_id: UUID
) -> dict[str, Any]:
    sources = await connection.fetch(
        """
        SELECT sitemap_url, discovered_via, status, declared_url_count,
               in_scope_url_count, truncated
        FROM sitemap_source
        WHERE tenant_id=$1 AND crawl_job_id=$2
        ORDER BY sitemap_url
        """,
        tenant_id, crawl_job_id,
    )
    totals = await connection.fetchrow(DECLARED_SUMMARY_SQL, tenant_id, crawl_job_id)
    not_crawled = await connection.fetch(
        DECLARED_NOT_CRAWLED_SQL, tenant_id, crawl_job_id, SAMPLE_LIMIT
    )
    not_indexable = await connection.fetch(
        DECLARED_NOT_INDEXABLE_SQL, tenant_id, crawl_job_id, SAMPLE_LIMIT
    )
    missing_from_sitemap = await connection.fetch(
        CRAWLED_NOT_DECLARED_SQL, tenant_id, crawl_job_id, SAMPLE_LIMIT
    )
    missing_count = await connection.fetchval(
        CRAWLED_NOT_DECLARED_COUNT_SQL, tenant_id, crawl_job_id
    )

    declared_total = int(totals["declared_total"]) if totals else 0
    declared_crawled = int(totals["declared_crawled"]) if totals else 0
    declared_indexable = int(totals["declared_indexable"]) if totals else 0
    crawled_total = int(totals["crawled_total"]) if totals else 0
    crawled_indexable = int(totals["crawled_indexable"]) if totals else 0

    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "crawl_job_id": str(crawl_job_id),
        "site_id": str(site_id),
        "sources": [
            {
                "sitemap_url": row["sitemap_url"],
                "discovered_via": row["discovered_via"],
                "status": row["status"],
                "declared_url_count": int(row["declared_url_count"]),
                "in_scope_url_count": int(row["in_scope_url_count"]),
                "truncated": bool(row["truncated"]),
            }
            for row in sources
        ],
        "totals": {
            "declared_in_scope": declared_total,
            "declared_and_crawled": declared_crawled,
            "declared_and_indexable": declared_indexable,
            "declared_not_crawled": declared_total - declared_crawled,
            "crawled_total": crawled_total,
            "crawled_indexable": crawled_indexable,
            "indexable_missing_from_sitemap": int(missing_count or 0),
            # Share of declared URLs the crawl both reached and found indexable.
            "declared_coverage_ratio": coverage_ratio(declared_indexable, declared_total),
            # Share of indexable pages the sitemap actually declares.
            "sitemap_completeness_ratio": coverage_ratio(declared_indexable, crawled_indexable),
        },
        "samples": {
            "limit": SAMPLE_LIMIT,
            "declared_not_crawled": [row["normalized_url"] for row in not_crawled],
            "declared_not_indexable": [
                {
                    "url": row["normalized_url"],
                    "http_status": row["http_status"],
                    "noindex": bool(row["noindex"]),
                    "canonical_url": row["canonical_url"],
                }
                for row in not_indexable
            ],
            "indexable_missing_from_sitemap": [
                row["normalized_url"] for row in missing_from_sitemap
            ],
        },
    }
