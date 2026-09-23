import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.analysis_rules import (
    LinkGraphEvidence,
    MultiAgentPageEvidence,
    PageEvidence,
    PerformanceEvidence,
    SearchConsoleEvidence,
    evaluate_multiagent_page,
    evaluate_page,
    opportunity_score,
)

DEFAULT_SCORING_VERSION = "multiagent-v1"
FALLBACK_SCORING_VERSION = "technical-v1"


class AnalysisConnection(Protocol):
    def transaction(self) -> Any: ...
    async def execute(self, query: str, *args: Any) -> str: ...
    async def fetch(self, query: str, *args: Any) -> list[Mapping[str, Any]]: ...
    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    run_id: UUID
    score_count: int
    finding_count: int
    opportunity_count: int
    already_completed: bool


def stable_hash(*parts: object) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def normalize_heading(text: str) -> str:
    """Compare headings by the words a reader sees, not by their whitespace."""
    return " ".join(text.split()).casefold()


def _pages_sharing_h1(row: Mapping[str, Any], counts: Mapping[str, int]) -> int:
    headings = [str(value) for value in parse_list(row["h1_json"])]
    if len(headings) != 1:
        return 1
    return counts.get(normalize_heading(headings[0]), 1)


async def analyze_crawl(
    connection: AnalysisConnection,
    tenant_id: UUID,
    crawl_id: UUID,
    scoring_version_code: str = DEFAULT_SCORING_VERSION,
) -> AnalysisResult:
    async with connection.transaction():
        await connection.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        crawl = await connection.fetchrow(
            """
            SELECT id,site_id,status FROM crawl_job
            WHERE id=$1 AND tenant_id=$2 AND status IN('completed','partial')
            """,
            crawl_id,
            tenant_id,
        )
        if crawl is None:
            raise ValueError("crawl_not_analyzable")

        observations = await connection.fetch(
            """
            SELECT o.id,o.page_id,o.observed_at,o.http_status,o.title,o.meta_description,
                   o.h1_json,o.word_count,o.canonical_url,o.robots_directives,o.content_hash,
                   o.structured_data_json,o.server_word_count,p.normalized_url
            FROM page_observation o
            JOIN page p ON p.id=o.page_id AND p.tenant_id=o.tenant_id
            WHERE o.tenant_id=$1 AND o.crawl_job_id=$2
            ORDER BY p.normalized_url,o.id
            """,
            tenant_id,
            crawl_id,
        )

        version = await connection.fetchrow(
            "SELECT id,code_version FROM scoring_version WHERE code_version=$1 AND retired_at IS NULL",
            scoring_version_code,
        )
        if version is None:
            version = await connection.fetchrow(
                "SELECT id,code_version FROM scoring_version WHERE code_version=$1 AND retired_at IS NULL",
                FALLBACK_SCORING_VERSION,
            )
        if version is None:
            raise RuntimeError("scoring_version_missing")

        active_version_code = version["code_version"]

        request_hash = stable_hash(
            active_version_code,
            [(row["id"], row["content_hash"], row["observed_at"]) for row in observations],
        )

        agent_type = "multiagent_rules" if active_version_code == "multiagent-v1" else "technical_rules"

        run = await connection.fetchrow(
            """
            INSERT INTO analysis_run(
              tenant_id,site_id,crawl_job_id,agent_type,agent_version,evidence_cutoff,request_hash,status
            ) VALUES($1,$2,$3,$4,$5,$6,$7,'running')
            ON CONFLICT(crawl_job_id,agent_type,agent_version,request_hash) DO NOTHING
            RETURNING id,status,finding_count,score_count,opportunity_count
            """,
            tenant_id,
            crawl["site_id"],
            crawl_id,
            agent_type,
            active_version_code,
            max((row["observed_at"] for row in observations), default=None),
            request_hash,
        )
        if run is None:
            existing = await connection.fetchrow(
                """
                SELECT id,status,finding_count,score_count,opportunity_count FROM analysis_run
                WHERE crawl_job_id=$1 AND agent_type=$2
                  AND agent_version=$3 AND request_hash=$4
                """,
                crawl_id,
                agent_type,
                active_version_code,
                request_hash,
            )
            if existing is None or existing["status"] != "completed":
                raise RuntimeError("analysis_run_conflict")
            return AnalysisResult(
                run_id=existing["id"],
                finding_count=existing["finding_count"],
                score_count=existing["score_count"],
                opportunity_count=existing["opportunity_count"],
                already_completed=True,
            )

        # Inbound links aggregation by target normalized url
        inbound_links_raw = await connection.fetch(
            """
            SELECT target_url, count(*)::int as inbound_count,
                   count(*) FILTER (WHERE anchor_text = '')::int as missing_anchors
            FROM link_edge
            WHERE tenant_id=$1 AND crawl_job_id=$2
            GROUP BY target_url
            """,
            tenant_id,
            crawl_id,
        )
        inbound_map = {row["target_url"]: (row["inbound_count"], row["missing_anchors"]) for row in inbound_links_raw}

        # Search console metrics aggregated by page_id
        gsc_metrics_raw = await connection.fetch(
            """
            SELECT page_id,
                   sum(clicks)::float as clicks,
                   sum(impressions)::float as impressions,
                   avg(position)::float as avg_position,
                   count(*) FILTER (WHERE position BETWEEN 10.1 AND 20.0 AND impressions >= 50)::int as striking_distance_count,
                   count(*) FILTER (WHERE position <= 10.0 AND ctr < 0.02 AND impressions >= 100)::int as low_ctr_count
            FROM search_metric
            WHERE tenant_id=$1 AND site_id=$2 AND page_id IS NOT NULL
            GROUP BY page_id
            """,
            tenant_id,
            crawl["site_id"],
        )
        gsc_map = {row["page_id"]: row for row in gsc_metrics_raw}

        # Performance observations by page_id (latest completed)
        perf_rows = await connection.fetch(
            """
            SELECT DISTINCT ON (page_id)
                   page_id, performance_score, lcp_ms, inp_ms, cls, ttfb_ms
            FROM performance_observation
            WHERE tenant_id=$1 AND site_id=$2
            ORDER BY page_id, observed_at DESC
            """,
            tenant_id,
            crawl["site_id"],
        )
        perf_map = {row["page_id"]: row for row in perf_rows}

        # An H1 says nothing on its own; it says something relative to the other
        # H1s on the site. Counting them once here keeps the per-page rules pure.
        h1_page_counts: dict[str, int] = defaultdict(int)
        for observation in observations:
            headings = [str(value) for value in parse_list(observation["h1_json"])]
            if len(headings) == 1:
                h1_page_counts[normalize_heading(headings[0])] += 1

        finding_count = 0
        opportunity_count = 0
        for row in observations:
            page_id = row["page_id"]
            normalized_url = str(row["normalized_url"])

            # This page has just been re-read, so everything open against it is
            # superseded by what the rules say now: an issue still present is
            # reopened by the upsert below, and one that was fixed stays closed.
            # Not filtered by scoring version. It used to be, and an opportunity
            # raised under an earlier version was never closed by any later
            # crawl -- the fingerprint includes the version, so the new version
            # opened a second copy beside it and both stayed in the queue.
            # Pages this crawl did not read are deliberately left alone: not
            # seeing a page is not evidence that its issue was fixed. The API
            # keeps those out of the default queue instead (see
            # OpportunityService.list_top).
            await connection.execute(
                """
                UPDATE finding SET status='resolved',last_seen_at=now()
                WHERE tenant_id=$1 AND page_id=$2 AND status='open'
                """,
                tenant_id,
                page_id,
            )
            await connection.execute(
                """
                UPDATE opportunity SET status='expired',updated_at=now()
                WHERE tenant_id=$1 AND page_id=$2 AND status='open'
                """,
                tenant_id,
                page_id,
            )

            page_evidence = PageEvidence(
                status=row["http_status"],
                title=row["title"],
                meta_description=row["meta_description"],
                h1=[str(value) for value in parse_list(row["h1_json"])],
                word_count=row["word_count"],
                canonical_url=row["canonical_url"],
                robots_directives=[str(value) for value in parse_list(row["robots_directives"])],
                structured_data=parse_list(row.get("structured_data_json", [])),
                pages_sharing_h1=_pages_sharing_h1(row, h1_page_counts),
                server_word_count=row.get("server_word_count"),
            )

            if active_version_code == "multiagent-v1":
                inbound_count, missing_anchors = inbound_map.get(normalized_url, (0, 0))
                gsc_row = gsc_map.get(page_id)
                perf_row = perf_map.get(page_id)

                multiagent_evidence = MultiAgentPageEvidence(
                    page_id=str(page_id),
                    normalized_url=normalized_url,
                    page=page_evidence,
                    link_graph=LinkGraphEvidence(
                        inbound_internal_links=inbound_count,
                        missing_anchor_inbound_count=missing_anchors,
                    ),
                    search_console=SearchConsoleEvidence(
                        clicks=gsc_row["clicks"] if gsc_row else 0.0,
                        impressions=gsc_row["impressions"] if gsc_row else 0.0,
                        ctr=(gsc_row["clicks"] / max(1.0, gsc_row["impressions"])) if gsc_row and gsc_row["impressions"] > 0 else None,
                        position=gsc_row["avg_position"] if gsc_row else None,
                        striking_distance_queries=gsc_row["striking_distance_count"] if gsc_row else 0,
                        low_ctr_queries=gsc_row["low_ctr_count"] if gsc_row else 0,
                    ),
                    performance=PerformanceEvidence(
                        performance_score=perf_row["performance_score"] if perf_row else None,
                        lcp_ms=perf_row["lcp_ms"] if perf_row else None,
                        inp_ms=perf_row["inp_ms"] if perf_row else None,
                        cls=perf_row["cls"] if perf_row else None,
                        ttfb_ms=perf_row["ttfb_ms"] if perf_row else None,
                    ),
                )
                page_score, findings = evaluate_multiagent_page(multiagent_evidence)
            else:
                page_score, findings = evaluate_page(page_evidence)

            await connection.execute(
                """
                INSERT INTO page_score(
                  tenant_id,page_id,observation_id,analysis_run_id,scoring_version_id,
                  score,factors_json,evidence_cutoff
                ) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                ON CONFLICT(page_id,scoring_version_id,observation_id) DO UPDATE SET
                  analysis_run_id=excluded.analysis_run_id,calculated_at=now(),score=excluded.score,
                  factors_json=excluded.factors_json,evidence_cutoff=excluded.evidence_cutoff
                """,
                tenant_id,
                page_id,
                row["id"],
                run["id"],
                version["id"],
                page_score,
                json.dumps({
                    "finding_codes": [finding.code for finding in findings],
                    "categories": list({finding.category for finding in findings}),
                }),
                row["observed_at"],
            )

            for finding in findings:
                fingerprint = stable_hash(active_version_code, page_id, finding.code)
                evidence_refs = {
                    "crawl_id": str(crawl_id),
                    "observation_id": str(row["id"]),
                    "page_id": str(page_id),
                    "scoring_version": active_version_code,
                    "category": finding.category,
                }
                stored = await connection.fetchrow(
                    """
                    INSERT INTO finding(
                      tenant_id,site_id,page_id,analysis_run_id,scoring_version_id,rule_key,
                      severity,category,evidence_refs,summary,confidence,status,fingerprint
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,'open',$12)
                    ON CONFLICT(tenant_id,fingerprint) DO UPDATE SET
                      analysis_run_id=excluded.analysis_run_id,severity=excluded.severity,
                      category=excluded.category,evidence_refs=excluded.evidence_refs,
                      summary=excluded.summary,confidence=excluded.confidence,
                      status='open',last_seen_at=now()
                    RETURNING id
                    """,
                    tenant_id,
                    crawl["site_id"],
                    page_id,
                    run["id"],
                    version["id"],
                    finding.code,
                    finding.severity,
                    finding.category,
                    json.dumps(evidence_refs),
                    finding.summary,
                    finding.confidence,
                    fingerprint,
                )
                if stored is None:
                    raise RuntimeError("finding_upsert_failed")

                opportunity_fingerprint = stable_hash("opportunity", fingerprint)
                ranked_score = opportunity_score(finding)
                opportunity = await connection.fetchrow(
                    """
                    INSERT INTO opportunity(
                      tenant_id,site_id,page_id,type,title,status,impact,confidence,urgency,
                      effort,risk,score,scoring_version_id,evidence_refs,fingerprint
                    ) VALUES($1,$2,$3,$4,$5,'open',$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14)
                    ON CONFLICT(tenant_id,fingerprint) DO UPDATE SET
                      type=excluded.type,title=excluded.title,status='open',impact=excluded.impact,
                      confidence=excluded.confidence,urgency=excluded.urgency,
                      effort=excluded.effort,risk=excluded.risk,score=excluded.score,
                      evidence_refs=excluded.evidence_refs,updated_at=now()
                    RETURNING id
                    """,
                    tenant_id,
                    crawl["site_id"],
                    page_id,
                    finding.category,
                    finding.summary,
                    finding.impact,
                    finding.confidence,
                    finding.urgency,
                    finding.effort,
                    finding.risk,
                    ranked_score,
                    version["id"],
                    json.dumps(evidence_refs),
                    opportunity_fingerprint,
                )
                if opportunity is None:
                    raise RuntimeError("opportunity_upsert_failed")

                await connection.execute(
                    """
                    INSERT INTO opportunity_finding(tenant_id,opportunity_id,finding_id)
                    VALUES($1,$2,$3) ON CONFLICT DO NOTHING
                    """,
                    tenant_id,
                    opportunity["id"],
                    stored["id"],
                )
                finding_count += 1
                opportunity_count += 1

        await connection.execute(
            """
            UPDATE analysis_run SET status='completed',finding_count=$2,score_count=$3,
              opportunity_count=$4,completed_at=now() WHERE id=$1
            """,
            run["id"],
            finding_count,
            len(observations),
            opportunity_count,
        )
        await connection.execute(
            """
            INSERT INTO outbox_event(
              tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload
            ) VALUES($1,'analysis.completed',1,'analysis_run',$2,$3::jsonb)
            """,
            tenant_id,
            run["id"],
            json.dumps(
                {
                    "analysis_run_id": str(run["id"]),
                    "crawl_id": str(crawl_id),
                    "site_id": str(crawl["site_id"]),
                    "scoring_version": active_version_code,
                    "finding_count": finding_count,
                    "opportunity_count": opportunity_count,
                }
            ),
        )
        return AnalysisResult(
            run_id=run["id"],
            score_count=len(observations),
            finding_count=finding_count,
            opportunity_count=opportunity_count,
            already_completed=False,
        )
