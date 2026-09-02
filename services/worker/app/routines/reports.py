"""Builds deterministic report payloads from stored evidence.

Every number here is read back from the evidence tables; nothing is estimated
and nothing is attributed causally. Search deltas are period-over-period
association, matching the language used everywhere else in the product.
"""

import hashlib
import json
from datetime import date, timedelta
from typing import Any, Protocol
from uuid import UUID

REPORT_SCHEMA_VERSION = 1
TOP_OPPORTUNITY_LIMIT = 10


class DatabaseConnection(Protocol):
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...


def content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _delta(current: float, previous: float) -> dict[str, float | None]:
    change = round(current - previous, 4)
    percent = round((change / previous) * 100, 2) if previous > 0 else None
    return {"current": round(current, 4), "previous": round(previous, 4),
            "change": change, "change_percent": percent}


async def _search_totals(
    connection: DatabaseConnection, tenant_id: UUID, site_id: UUID, start: date, end: date
) -> dict[str, float]:
    row = await connection.fetchrow(
        """
        SELECT COALESCE(SUM(clicks),0) AS clicks,
               COALESCE(SUM(impressions),0) AS impressions,
               COALESCE(AVG(position),0) AS position
        FROM search_metric
        WHERE tenant_id=$1 AND site_id=$2 AND metric_date BETWEEN $3 AND $4
        """,
        tenant_id, site_id, start, end,
    )
    if row is None:
        return {"clicks": 0.0, "impressions": 0.0, "position": 0.0}
    return {
        "clicks": float(row["clicks"]),
        "impressions": float(row["impressions"]),
        "position": float(row["position"]),
    }


async def build_weekly_digest(
    connection: DatabaseConnection,
    tenant_id: UUID,
    site_id: UUID,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    span = (period_end - period_start).days + 1
    prior_end = period_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=span - 1)

    site = await connection.fetchrow(
        "SELECT name,canonical_origin,mode,status FROM site WHERE id=$1 AND tenant_id=$2",
        site_id, tenant_id,
    )
    if site is None:
        raise ValueError("site_not_found")

    crawl = await connection.fetchrow(
        """
        SELECT id,status,finished_at,
               COALESCE((config_snapshot->>'max_pages')::int,0) AS max_pages
        FROM crawl_job
        WHERE tenant_id=$1 AND site_id=$2
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        tenant_id, site_id,
    )
    pages_known = await connection.fetchval(
        "SELECT count(*) FROM page WHERE tenant_id=$1 AND site_id=$2", tenant_id, site_id
    )

    by_category = await connection.fetch(
        """
        SELECT type AS category, count(*) AS open_count, round(AVG(score)::numeric,2) AS avg_score
        FROM opportunity
        WHERE tenant_id=$1 AND site_id=$2 AND status='open'
          AND suppressed_reason IS NULL AND risk<>'prohibited'
        GROUP BY type ORDER BY type
        """,
        tenant_id, site_id,
    )
    top = await connection.fetch(
        """
        SELECT o.title, o.type AS category, o.score, o.risk, o.confidence, p.normalized_url
        FROM opportunity o
        JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
        WHERE o.tenant_id=$1 AND o.site_id=$2 AND o.status='open'
          AND o.suppressed_reason IS NULL AND o.risk<>'prohibited'
        ORDER BY o.score DESC, o.fingerprint
        LIMIT $3
        """,
        tenant_id, site_id, TOP_OPPORTUNITY_LIMIT,
    )
    opened = await connection.fetchval(
        """
        SELECT count(*) FROM opportunity
        WHERE tenant_id=$1 AND site_id=$2 AND created_at::date BETWEEN $3 AND $4
        """,
        tenant_id, site_id, period_start, period_end,
    )
    resolved = await connection.fetchval(
        """
        SELECT count(*) FROM opportunity
        WHERE tenant_id=$1 AND site_id=$2 AND status IN('accepted','dismissed')
          AND updated_at::date BETWEEN $3 AND $4
        """,
        tenant_id, site_id, period_start, period_end,
    )

    current = await _search_totals(connection, tenant_id, site_id, period_start, period_end)
    previous = await _search_totals(connection, tenant_id, site_id, prior_start, prior_end)
    has_search_evidence = current["impressions"] > 0 or previous["impressions"] > 0

    performance = await connection.fetchrow(
        """
        SELECT round(AVG(performance_score)::numeric,1) AS performance_score,
               round(AVG(lcp_ms)::numeric,0) AS lcp_ms,
               round(AVG(cls)::numeric,3) AS cls,
               count(*) AS sampled_pages
        FROM performance_observation
        WHERE tenant_id=$1 AND site_id=$2 AND observed_at::date BETWEEN $3 AND $4
        """,
        tenant_id, site_id, period_start, period_end,
    )

    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "site": {
            "name": site["name"],
            "canonical_origin": site["canonical_origin"],
            "mode": site["mode"],
            "status": site["status"],
        },
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "days": span,
            "comparison_start": prior_start.isoformat(),
            "comparison_end": prior_end.isoformat(),
        },
        "crawl": {
            "latest_status": crawl["status"] if crawl else None,
            "latest_finished_at": crawl["finished_at"].isoformat()
            if crawl and crawl["finished_at"]
            else None,
            "pages_known": int(pages_known or 0),
        },
        "opportunities": {
            "open_by_category": [
                {
                    "category": row["category"],
                    "open_count": int(row["open_count"]),
                    "average_score": float(row["avg_score"] or 0),
                }
                for row in by_category
            ],
            "opened_in_period": int(opened or 0),
            "resolved_in_period": int(resolved or 0),
            "top": [
                {
                    "title": row["title"],
                    "category": row["category"],
                    "url": row["normalized_url"],
                    "score": float(row["score"]),
                    "confidence": float(row["confidence"]),
                    "risk": row["risk"],
                }
                for row in top
            ],
        },
        "search": {
            "available": has_search_evidence,
            "clicks": _delta(current["clicks"], previous["clicks"]),
            "impressions": _delta(current["impressions"], previous["impressions"]),
            "average_position": _delta(current["position"], previous["position"]),
            # Deltas describe association over the window, not attribution.
            "interpretation": "period_over_period_association",
        }
        if has_search_evidence
        else {"available": False, "reason": "no_search_console_evidence_in_window"},
        "performance": {
            "available": bool(performance and performance["sampled_pages"]),
            "average_score": float(performance["performance_score"])
            if performance and performance["performance_score"] is not None
            else None,
            "average_lcp_ms": float(performance["lcp_ms"])
            if performance and performance["lcp_ms"] is not None
            else None,
            "average_cls": float(performance["cls"])
            if performance and performance["cls"] is not None
            else None,
            "sampled_pages": int(performance["sampled_pages"]) if performance else 0,
        },
    }
    return payload
