"""Runs one competitor scan and reports structural gaps against our own pages.

Every URL requested is one a human put on record. Robots is fetched once per
competitor host and applied to each path; a disallowed page is recorded as such
rather than fetched anyway.
"""

import json
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx

from app.competitors.fetch import (
    MAX_PAGES_PER_COMPETITOR,
    USER_AGENT,
    Fetcher,
    observe_competitor_page,
)
from app.egress.guard import EgressBlocked, assert_public_https_target

logger = logging.getLogger(__name__)

LOAD_PAGES_SQL = """
SELECT cp.id, cp.normalized_url, cp.keyword_cluster_key,
       c.id AS competitor_id, c.normalized_host, c.label
FROM competitor_page cp
JOIN competitor c ON c.id=cp.competitor_id AND c.tenant_id=cp.tenant_id
WHERE cp.tenant_id=$1 AND cp.site_id=$2 AND cp.status='active' AND c.status='active'
ORDER BY c.normalized_host, cp.normalized_url
"""


async def _robots_for_host(fetcher: Fetcher, host: str) -> str:
    url = urlunsplit(("https", host, "/robots.txt", "", ""))
    try:
        assert_public_https_target(url)
        response = await fetcher.get(
            url, timeout=10.0, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"}
        )
    except (EgressBlocked, httpx.HTTPError, OSError, ValueError, TimeoutError):
        # An absent or unreachable robots.txt is not fatal; the conservative
        # parser treats empty text as "no rules stated".
        return ""
    if int(getattr(response, "status_code", 0)) >= 400:
        return ""
    return (getattr(response, "text", "") or "")[:512_000]


async def run_competitor_scan(
    connection: Any,
    fetcher: Fetcher,
    tenant_id: UUID,
    site_id: UUID,
    routine_run_id: UUID | None = None,
) -> dict[str, Any]:
    rows = await connection.fetch(LOAD_PAGES_SQL, tenant_id, site_id)
    if not rows:
        return {"pages_requested": 0, "reason": "no_competitor_pages"}

    scan_id = await connection.fetchval(
        """
        INSERT INTO competitor_scan(tenant_id,site_id,routine_run_id,status,pages_requested)
        VALUES($1,$2,$3,'running',$4) RETURNING id
        """,
        tenant_id, site_id, routine_run_id, len(rows),
    )

    robots_cache: dict[str, str] = {}
    per_competitor: dict[UUID, int] = {}
    observed = blocked = failed = 0

    for row in rows:
        competitor_id = row["competitor_id"]
        seen = per_competitor.get(competitor_id, 0)
        if seen >= MAX_PAGES_PER_COMPETITOR:
            continue
        per_competitor[competitor_id] = seen + 1

        host = row["normalized_host"]
        if host not in robots_cache:
            robots_cache[host] = await _robots_for_host(fetcher, host)
        evidence = await observe_competitor_page(
            fetcher, row["normalized_url"], host, robots_cache[host]
        )
        if evidence.outcome == "observed":
            observed += 1
        elif evidence.outcome == "robots_disallowed":
            blocked += 1
        else:
            failed += 1

        await connection.execute(
            """
            INSERT INTO competitor_observation(
              tenant_id,competitor_scan_id,competitor_page_id,site_id,outcome,http_status,
              title,meta_description,h1_json,heading_count,word_count,internal_link_count,
              structured_data_types,content_hash)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14)
            ON CONFLICT(competitor_scan_id,competitor_page_id) DO NOTHING
            """,
            tenant_id, scan_id, row["id"], site_id, evidence.outcome, evidence.http_status,
            evidence.title, evidence.meta_description,
            json.dumps(evidence.h1, separators=(",", ":")),
            evidence.heading_count, evidence.word_count, evidence.internal_link_count,
            evidence.structured_data_types, evidence.content_hash,
        )

    status = "completed" if failed == 0 else "partial"
    await connection.execute(
        """
        UPDATE competitor_scan SET status=$3,finished_at=now(),
          pages_observed=$4,pages_blocked=$5,pages_failed=$6
        WHERE id=$1 AND tenant_id=$2
        """,
        scan_id, tenant_id, status, observed, blocked, failed,
    )
    return {
        "competitor_scan_id": str(scan_id),
        "status": status,
        "pages_requested": len(rows),
        "pages_observed": observed,
        "pages_blocked_by_robots": blocked,
        "pages_failed": failed,
    }


def normalize_competitor_url(raw: str) -> tuple[str, str]:
    """Return (normalized_url, host). Rejects anything not a plain https page."""
    parsed = urlsplit(raw.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("competitor_url_must_be_https")
    if parsed.username or parsed.password:
        raise ValueError("competitor_url_must_not_embed_credentials")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("competitor_url_host_not_public")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parsed.query, "")), host
