"""Answer-engine readiness from first-party evidence.

This measures whether the site is *positioned* to be cited by an answer engine:
whether its question-bearing topics are answered on crawlable, indexable pages
carrying the structured data those surfaces read.

It deliberately does not claim to observe citations. Knowing what an answer
engine actually said requires a certified external provider, which is not
integrated; `citation_source` records `none` so a reader cannot mistake
readiness for measured visibility.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

READINESS_SCHEMA_VERSION = 2

# Structured data types answer surfaces read for direct question answering.
ANSWER_ENGINE_TYPES = ("FAQPage", "QAPage", "HowTo")
# Types that establish the entity behind an answer.
ENTITY_TYPES = ("Organization", "Person", "WebSite", "Article", "BreadcrumbList")

# Factor weights. They sum to 1.0; each is measured, none is assumed.
FACTOR_WEIGHTS = {
    "crawlable_indexable": 0.25,
    "entity_markup": 0.15,
    "question_answer_markup": 0.20,
    "question_topic_coverage": 0.20,
    "ai_crawler_access": 0.20,
}

# The crawler records the site's robots.txt policy per AI user agent. Only
# retrieval bots -- the ones that fetch a page to answer and cite -- are scored;
# refusing training bots is a legitimate choice with no bearing on citation.
CRAWL_ACCESS_SQL = """
SELECT result_summary->'ai_access' AS ai_access
FROM crawl_job WHERE tenant_id=$1 AND id=$2
"""

PAGE_EVIDENCE_SQL = """
WITH latest AS (
  SELECT DISTINCT ON (o.page_id) o.page_id, o.http_status, o.robots_directives,
         o.canonical_url, o.structured_data_json, p.normalized_url
  FROM page_observation o
  JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
  WHERE o.tenant_id=$1 AND o.crawl_job_id=$2
  ORDER BY o.page_id, o.observed_at DESC
)
SELECT count(*) AS total,
       count(*) FILTER (
         WHERE http_status=200
           AND NOT (robots_directives @> ARRAY['noindex'])
           AND (canonical_url IS NULL OR canonical_url=normalized_url)
       ) AS indexable,
       count(*) FILTER (WHERE structured_data_json::text ~ $3) AS answer_markup,
       count(*) FILTER (WHERE structured_data_json::text ~ $4) AS entity_markup
FROM latest
"""


def _type_pattern(types: tuple[str, ...]) -> str:
    return "|".join(f'"@type"\\s*:\\s*"{name}"' for name in types)


def content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole > 0 else 0.0


def crawler_access_factor(ai_access: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Score retrieval-bot access and describe llms.txt, from the crawl summary.

    Crawls recorded before the crawler measured this carry no report; the
    factor is then unmeasured rather than scored as blocked.
    """
    if isinstance(ai_access, str):
        ai_access = json.loads(ai_access)
    crawlers = ai_access.get("crawlers") if isinstance(ai_access, dict) else None
    if not isinstance(crawlers, list) or ai_access.get("robots_txt") == "unreachable":
        return {"value": 0.0, "measured": False, "detail": {}}, None

    def share(row: dict[str, Any]) -> float:
        sampled = int(row.get("sampled_urls") or 0)
        return int(row.get("allowed_urls") or 0) / sampled if sampled else 1.0

    def summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "token": str(row.get("token")),
            "operator": str(row.get("operator")),
            "allowed_share": round(share(row), 4),
            "homepage_allowed": bool(row.get("homepage_allowed")),
        }

    rows = [row for row in crawlers if isinstance(row, dict)]
    retrieval = [row for row in rows if row.get("purpose") == "retrieval"]
    training = [row for row in rows if row.get("purpose") == "training"]
    value = round(sum(share(row) for row in retrieval) / len(retrieval), 4) if retrieval else 0.0
    factor = {
        "value": value,
        "measured": bool(retrieval),
        "detail": {
            "robots_txt": ai_access.get("robots_txt"),
            "retrieval_blocked": [summary(row) for row in retrieval if share(row) < 1.0],
            "retrieval_checked": len(retrieval),
            # Reported so the choice is visible; never part of the score.
            "training_blocked": [summary(row) for row in training if share(row) < 1.0],
            "source": "robots.txt policy; firewall or CDN blocks are not observable",
        },
    }
    llms = ai_access.get("llms_txt")
    return factor, llms if isinstance(llms, dict) else None


async def build_ai_visibility_snapshot(
    connection: Any, tenant_id: UUID, site_id: UUID, crawl_job_id: UUID
) -> dict[str, Any]:
    pages = await connection.fetchrow(
        PAGE_EVIDENCE_SQL,
        tenant_id, crawl_job_id,
        _type_pattern(ANSWER_ENGINE_TYPES), _type_pattern(ENTITY_TYPES),
    )
    total = int(pages["total"]) if pages else 0
    indexable = int(pages["indexable"]) if pages else 0
    answer_markup = int(pages["answer_markup"]) if pages else 0
    entity_markup = int(pages["entity_markup"]) if pages else 0

    # Question-bearing clusters and whether each already has an answering page
    # that carries answer markup.
    clusters = await connection.fetchrow(
        """
        WITH latest_run AS (
          SELECT id FROM keyword_analysis_run
          WHERE tenant_id=$1 AND site_id=$2 AND status='completed'
          ORDER BY created_at DESC, id DESC LIMIT 1
        )
        SELECT
          count(*) FILTER (WHERE c.answer_engine_candidate) AS question_clusters,
          count(*) FILTER (
            WHERE c.answer_engine_candidate AND c.primary_page_id IS NOT NULL
          ) AS answered_clusters
        FROM keyword_cluster c
        WHERE c.tenant_id=$1 AND c.site_id=$2
          AND c.analysis_run_id=(SELECT id FROM latest_run)
        """,
        tenant_id, site_id,
    )
    access_row = await connection.fetchrow(CRAWL_ACCESS_SQL, tenant_id, crawl_job_id)
    access_factor, llms_txt = crawler_access_factor(
        access_row["ai_access"] if access_row else None
    )

    question_clusters = int(clusters["question_clusters"]) if clusters else 0
    answered_clusters = int(clusters["answered_clusters"]) if clusters else 0

    factors = {
        "crawlable_indexable": {
            "value": _ratio(indexable, total),
            "measured": total > 0,
            "detail": {"indexable_pages": indexable, "pages_observed": total},
        },
        "entity_markup": {
            "value": _ratio(entity_markup, total),
            "measured": total > 0,
            "detail": {"pages_with_entity_markup": entity_markup, "types": list(ENTITY_TYPES)},
        },
        "question_answer_markup": {
            "value": _ratio(answer_markup, total),
            "measured": total > 0,
            "detail": {
                "pages_with_answer_markup": answer_markup,
                "types": list(ANSWER_ENGINE_TYPES),
            },
        },
        "question_topic_coverage": {
            "value": _ratio(answered_clusters, question_clusters),
            # Unmeasured when no keyword analysis has run, rather than scored 0.
            "measured": question_clusters > 0,
            "detail": {
                "question_clusters": question_clusters,
                "clusters_with_answering_page": answered_clusters,
            },
        },
        "ai_crawler_access": access_factor,
    }

    # Only measured factors contribute, and the weights are renormalised over
    # them, so a missing input lowers confidence rather than faking a low score.
    measured_weight = sum(
        weight for name, weight in FACTOR_WEIGHTS.items() if factors[name]["measured"]
    )
    if measured_weight > 0:
        score = round(
            sum(
                FACTOR_WEIGHTS[name] * float(factors[name]["value"])
                for name in FACTOR_WEIGHTS
                if factors[name]["measured"]
            )
            / measured_weight
            * 100,
            2,
        )
    else:
        score = 0.0

    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "crawl_job_id": str(crawl_job_id),
        "readiness_score": score,
        "measured_weight": round(measured_weight, 4),
        "factors": factors,
        "weights": FACTOR_WEIGHTS,
        # Reported, not scored: no answer engine has said it reads llms.txt.
        "llms_txt": llms_txt,
        # Stated on every snapshot so readiness is never read as observed
        # answer-engine visibility.
        "citation_source": "none",
        "interpretation": (
            "Readiness measured from first-party crawl and search evidence. "
            "It does not observe answer-engine citations; that requires a "
            "certified external provider, which is not integrated."
        ),
    }
