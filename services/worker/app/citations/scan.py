"""Ask each tracked question, and record whether the answer cited the site.

The model calls happen before any transaction opens (`collect`); the results
are written afterwards in one (`persist`), so a slow provider never holds
locks. Spend is capped per workspace per month, and the cap is applied before
any call is made: the scan asks only as many questions as the remaining budget
covers at a deliberately high per-answer estimate.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from app.citations.models import CitationModel, CitationModelError

MAX_PROMPTS = 8
CONCURRENCY = 6
# What one answer is assumed to cost when deciding how many fit the budget.
# Well above what a low-effort answer with three searches costs, so the cap
# holds even when a provider's answer runs long.
ESTIMATE_PER_ANSWER_MICROS = 200_000
MAX_EXCERPT = 600
MAX_HOSTS = 20
CREDENTIALS = {"anthropic": "anthropic_api_key", "openai": "openai_api_key"}


def normalize_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    return host.removeprefix("www.")


def host_of(url: str) -> str:
    try:
        return normalize_host(urlsplit(url).hostname or "")
    except ValueError:
        return ""


def _belongs(host: str, site_host: str) -> bool:
    return bool(host) and (host == site_host or host.endswith("." + site_host))


def brand_terms(site_name: str, site_host: str) -> list[str]:
    """Names a reader would recognise the site by: its name and its bare domain label."""
    terms = {site_host}
    name = " ".join(site_name.split())
    if len(name) >= 4:
        terms.add(name.lower())
    label = site_host.split(".")[0]
    if len(label) >= 4:
        terms.add(label)
    return sorted(terms, key=len, reverse=True)


def _flatten(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class CitationFinding:
    site_cited: bool
    site_mentioned: bool
    own_citation_rank: int | None
    cited_hosts: list[str]
    own_urls: list[str]
    competitor_hosts: list[str]
    excerpt: str


def analyze(
    text: str,
    cited_urls: list[str],
    site_host: str,
    terms: list[str],
    competitors: list[str],
) -> CitationFinding:
    site_host = normalize_host(site_host)
    hosts: list[str] = []
    for url in cited_urls:
        host = host_of(url)
        if host and host not in hosts:
            hosts.append(host)
    rank = next((index + 1 for index, host in enumerate(hosts) if _belongs(host, site_host)), None)
    own_urls: list[str] = []
    for url in cited_urls:
        if _belongs(host_of(url), site_host) and url not in own_urls:
            own_urls.append(url[:500])
    competitor_set = {normalize_host(host) for host in competitors}
    cited_competitors = [
        host for host in hosts if any(_belongs(host, competitor) for competitor in competitor_set)
    ]

    flat = _flatten(text)
    lowered = flat.lower()
    match = None
    for term in terms:
        match = re.search(rf"(?<![\w.-]){re.escape(term)}(?![\w-]|\.\w)", lowered)
        if match:
            break
    if match:
        start = max(0, match.start() - 200)
        excerpt = ("…" if start else "") + flat[start : start + MAX_EXCERPT - 2]
    else:
        excerpt = flat[: MAX_EXCERPT - 1]
    if len(excerpt) > MAX_EXCERPT:
        excerpt = excerpt[: MAX_EXCERPT - 1] + "…"
    return CitationFinding(
        site_cited=rank is not None,
        site_mentioned=match is not None,
        own_citation_rank=rank,
        cited_hosts=hosts[:MAX_HOSTS],
        own_urls=own_urls[:5],
        competitor_hosts=cited_competitors[:MAX_HOSTS],
        excerpt=excerpt,
    )


@dataclass(slots=True)
class Observation:
    prompt_id: UUID
    prompt: str
    provider: str
    model: str
    status: str
    error_code: str | None = None
    finding: CitationFinding | None = None
    web_searches: int = 0
    cost_micros: int = 0


@dataclass(slots=True)
class Collected:
    skip: str | None = None
    observations: list[Observation] = field(default_factory=list)
    prompts_tracked: int = 0
    not_asked_for_budget: int = 0


def calls_within_budget(requested: int, spent_micros: int, budget_micros: int) -> int:
    remaining = max(0, budget_micros - spent_micros)
    return min(requested, remaining // ESTIMATE_PER_ANSWER_MICROS)


async def collect(
    connection: Any,
    tenant_id: UUID,
    site_id: UUID,
    *,
    models: dict[str, tuple[CitationModel, str]],
    read_key: Any,
    monthly_budget_micros: int,
) -> Collected:
    """Ask every tracked question of every provider the workspace has a key for.

    `read_key(provider_credential)` returns the workspace's decrypted key or
    None. Keys live only in this call's locals.
    """
    site = await connection.fetchrow(
        "SELECT name,normalized_host FROM site WHERE id=$1 AND tenant_id=$2", site_id, tenant_id
    )
    if site is None:
        return Collected(skip="site_not_found")
    prompts = await connection.fetch(
        """
        SELECT id,prompt FROM ai_citation_prompt
        WHERE tenant_id=$1 AND site_id=$2 AND active
        ORDER BY created_at, id LIMIT $3
        """,
        tenant_id, site_id, MAX_PROMPTS,
    )
    if not prompts:
        return Collected(skip="no_tracked_questions")
    keys: dict[str, str] = {}
    for provider in models:
        key = await read_key(CREDENTIALS[provider])
        if key:
            keys[provider] = key
    if not keys:
        return Collected(skip="no_ai_key", prompts_tracked=len(prompts))

    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = await connection.fetchval(
        "SELECT coalesce(sum(cost_micros),0) FROM ai_citation_run WHERE tenant_id=$1 AND started_at>=$2",
        tenant_id, month_start,
    )
    jobs = [(row, provider) for row in prompts for provider in sorted(keys)]
    allowed = calls_within_budget(len(jobs), int(spent or 0), monthly_budget_micros)
    if allowed == 0:
        return Collected(skip="ai_citation_budget_exhausted", prompts_tracked=len(prompts))

    host = normalize_host(str(site["normalized_host"]))
    terms = brand_terms(str(site["name"]), host)
    competitors = [
        str(row["normalized_host"])
        for row in await connection.fetch(
            "SELECT normalized_host FROM competitor WHERE tenant_id=$1 AND site_id=$2 AND status='active'",
            tenant_id, site_id,
        )
    ]
    gate = asyncio.Semaphore(CONCURRENCY)

    async def ask(row: Any, provider: str) -> Observation:
        client, model = models[provider]
        base = Observation(row["id"], str(row["prompt"]), provider, model, "failed")
        async with gate:
            try:
                answer = await client.ask(api_key=keys[provider], model=model, question=str(row["prompt"]))
            except CitationModelError as error:
                base.error_code = error.code
                return base
            except Exception:  # noqa: BLE001 - one bad answer must not sink the scan
                base.error_code = "provider_error"
                return base
        base.status = "answered"
        base.model = answer.model[:120]
        base.web_searches = answer.web_searches
        base.cost_micros = answer.cost_micros
        base.finding = analyze(answer.text, answer.cited_urls, host, terms, competitors)
        return base

    observations = await asyncio.gather(*(ask(row, provider) for row, provider in jobs[:allowed]))
    return Collected(
        observations=list(observations),
        prompts_tracked=len(prompts),
        not_asked_for_budget=len(jobs) - allowed,
    )


async def persist(
    connection: Any,
    tenant_id: UUID,
    site_id: UUID,
    routine_run_id: UUID,
    collected: Collected,
) -> tuple[str, dict[str, Any], str | None]:
    if collected.skip:
        return "skipped", {"prompts_tracked": collected.prompts_tracked}, collected.skip
    answered = [item for item in collected.observations if item.status == "answered"]
    cited = sum(1 for item in answered if item.finding and item.finding.site_cited)
    mentioned = sum(1 for item in answered if item.finding and item.finding.site_mentioned)
    cost = sum(item.cost_micros for item in collected.observations)
    by_provider: dict[str, dict[str, int]] = {}
    for item in collected.observations:
        row = by_provider.setdefault(item.provider, {"asked": 0, "answered": 0, "cited": 0, "mentioned": 0})
        row["asked"] += 1
        if item.status == "answered" and item.finding:
            row["answered"] += 1
            row["cited"] += int(item.finding.site_cited)
            row["mentioned"] += int(item.finding.site_mentioned)
    errors: dict[str, int] = {}
    for item in collected.observations:
        if item.error_code:
            errors[item.error_code] = errors.get(item.error_code, 0) + 1
    summary = {
        "by_provider": by_provider,
        "errors": errors,
        "not_asked_for_budget": collected.not_asked_for_budget,
        "source": "provider API with web search; answers vary between runs",
    }
    run_id = await connection.fetchval(
        """
        INSERT INTO ai_citation_run(tenant_id,site_id,routine_run_id,finished_at,prompts_asked,
          answers,cited,mentioned,cost_micros,summary_json)
        VALUES($1,$2,$3,now(),$4,$5,$6,$7,$8,$9::jsonb) RETURNING id
        """,
        tenant_id, site_id, routine_run_id, len(collected.observations), len(answered),
        cited, mentioned, cost, json.dumps(summary, sort_keys=True),
    )
    for item in collected.observations:
        finding = item.finding
        await connection.execute(
            """
            INSERT INTO ai_citation_observation(tenant_id,site_id,run_id,prompt_id,prompt,provider,
              model,status,error_code,site_cited,site_mentioned,own_citation_rank,cited_hosts,
              own_urls,competitor_hosts,answer_excerpt,web_searches,cost_micros)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb,$15::jsonb,$16,$17,$18)
            """,
            tenant_id, site_id, run_id, item.prompt_id, item.prompt, item.provider, item.model,
            item.status, item.error_code,
            bool(finding and finding.site_cited), bool(finding and finding.site_mentioned),
            finding.own_citation_rank if finding else None,
            json.dumps(finding.cited_hosts if finding else []),
            json.dumps(finding.own_urls if finding else []),
            json.dumps(finding.competitor_hosts if finding else []),
            finding.excerpt if finding else None,
            item.web_searches, item.cost_micros,
        )
    # Per-answer failures are recorded on each observation; the run itself
    # completed, and its summary says which errors stopped which answers.
    return "completed", {
        "ai_citation_run_id": str(run_id),
        "answers": len(answered),
        "cited": cited,
        "mentioned": mentioned,
        "cost_micros": cost,
        "errors": errors,
    }, None
