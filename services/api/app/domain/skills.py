"""The skill registry and the deterministic router that selects one.

A chat message is untrusted input. It never becomes tool authority: the router
can only return a skill from this fixed registry, and the caller re-checks the
actor's role before executing, so the workspace reaches exactly what the same
actor could already reach through the API and nothing more.

Routing is scored token overlap, not a model call, so the same phrasing always
selects the same skill and an unclear request is answered with a question
instead of a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.context import Role


class SkillEffect(StrEnum):
    # Answers from stored evidence.
    READ = "read"
    # Queues a routine run; the work happens in the worker under its own gates.
    SCHEDULE = "schedule"


@dataclass(frozen=True, slots=True)
class Skill:
    key: str
    name: str
    description: str
    effect: SkillEffect
    # Roles permitted to invoke it. Enforced again at execution.
    allowed_roles: frozenset[Role]
    # Terms that select this skill, weighted by how specific they are.
    strong_triggers: frozenset[str] = field(default_factory=frozenset)
    weak_triggers: frozenset[str] = field(default_factory=frozenset)
    # Routine kind queued when effect is SCHEDULE.
    routine_kind: str | None = None
    example: str = ""


ALL_ROLES = frozenset(Role)
ACTING_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.SEO_MANAGER})
CONTENT_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR})

SKILLS: tuple[Skill, ...] = (
    Skill(
        key="site_audit",
        name="Website SEO audit",
        description=(
            "Report the site's current technical, content, linking, performance, and "
            "answer-engine findings from the latest crawl, ranked by opportunity score."
        ),
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"audit", "issues", "findings", "problems"}),
        weak_triggers=frozenset(
            {"seo", "site", "technical", "health", "score", "opportunities", "opportunity"}
        ),
        example="Audit the site and show the top issues.",
    ),
    Skill(
        key="run_site_audit",
        name="Run a new crawl",
        description="Queue a fresh crawl so the next audit reads current evidence.",
        effect=SkillEffect.SCHEDULE,
        allowed_roles=ACTING_ROLES,
        routine_kind="site_audit",
        strong_triggers=frozenset({"recrawl", "crawl", "rescan"}),
        weak_triggers=frozenset({"run", "start", "new", "again", "now", "refresh"}),
        example="Run a new crawl of the site.",
    ),
    Skill(
        key="keyword_research",
        name="SEO and AEO keyword research",
        description=(
            "Show the site's keyword clusters with intent, answer-engine candidacy, "
            "striking-distance counts, and opportunity score."
        ),
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"keyword", "keywords", "cluster", "clusters", "queries", "intent"}),
        weak_triggers=frozenset({"search", "demand", "ranking", "terms", "topics"}),
        example="What keyword opportunities do we have?",
    ),
    Skill(
        key="run_keyword_research",
        name="Recluster keyword demand",
        description="Rebuild keyword clusters from the most recent search evidence.",
        effect=SkillEffect.SCHEDULE,
        allowed_roles=ACTING_ROLES,
        routine_kind="keyword_refresh",
        strong_triggers=frozenset({"recluster", "recompute"}),
        weak_triggers=frozenset({"keyword", "keywords", "refresh", "update", "rebuild"}),
        example="Recluster our keywords with the latest data.",
    ),
    Skill(
        key="content_briefs",
        name="Content briefs and refresh queue",
        description=(
            "Show the prioritised brief queue: which pages to refresh, which pages to "
            "create, and what each brief asks for."
        ),
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"brief", "briefs", "refresh queue", "content plan", "rewrite"}),
        weak_triggers=frozenset({"content", "queue", "plan", "write", "page"}),
        example="What content briefs are queued?",
    ),
    Skill(
        key="run_content_briefs",
        name="Regenerate content briefs",
        description="Rebuild briefs from the latest keyword clusters and page evidence.",
        effect=SkillEffect.SCHEDULE,
        allowed_roles=CONTENT_ROLES,
        routine_kind="content_briefs",
        strong_triggers=frozenset({"regenerate", "rebuild"}),
        weak_triggers=frozenset({"brief", "briefs", "generate", "again"}),
        example="Regenerate the content briefs.",
    ),
    Skill(
        key="sitemap_review",
        name="SEO sitemap coverage",
        description=(
            "Compare what the sitemaps declare against what the crawl reached and what is "
            "indexable, and list the gaps in each direction."
        ),
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"sitemap", "sitemaps", "coverage", "indexable", "indexed"}),
        weak_triggers=frozenset({"urls", "pages", "declared", "missing"}),
        example="How is our sitemap coverage?",
    ),
    Skill(
        key="competitor_pages",
        name="SEO competitor pages",
        description=(
            "Compare tracked competitor pages against our own on title, description, "
            "headings, depth, structured data, and internal linking."
        ),
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"competitor", "competitors", "rival", "rivals", "compare"}),
        weak_triggers=frozenset({"gap", "gaps", "versus", "against", "them"}),
        example="How do we compare to the competitors we track?",
    ),
    Skill(
        key="run_competitor_scan",
        name="Scan tracked competitors",
        description="Refetch the competitor pages already on record. No new URLs are discovered.",
        effect=SkillEffect.SCHEDULE,
        allowed_roles=ACTING_ROLES,
        routine_kind="competitor_scan",
        strong_triggers=frozenset({"scan", "refetch"}),
        weak_triggers=frozenset({"competitor", "competitors", "check", "monitor", "now"}),
        example="Scan the competitors we track.",
    ),
    Skill(
        key="ai_visibility",
        name="AI search visibility readiness",
        description=(
            "Report answer-engine readiness measured from our own crawl and search "
            "evidence. It does not observe citations from any answer engine."
        ),
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"ai visibility", "answer engine", "aeo", "geo", "llm", "chatgpt"}),
        weak_triggers=frozenset({"ai", "visibility", "citation", "citations", "readiness"}),
        example="What is our AI search visibility readiness?",
    ),
    Skill(
        key="weekly_report",
        name="Weekly report",
        description="Show the latest weekly digest: what changed, what opened, what resolved.",
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"report", "digest", "summary", "weekly"}),
        weak_triggers=frozenset({"week", "changed", "progress", "update"}),
        example="Show me the latest weekly report.",
    ),
    Skill(
        key="routines",
        name="Scheduled routines",
        description="Show which routines are scheduled, when they next run, and how the last run ended.",
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"routine", "routines", "schedule", "scheduled", "cron"}),
        weak_triggers=frozenset({"automation", "recurring", "next", "when"}),
        example="What routines are scheduled?",
    ),
)

SKILLS_BY_KEY = {skill.key: skill for skill in SKILLS}

# A scheduling skill needs a clearly imperative request; below this the router
# asks rather than starting work nobody asked for.
SCHEDULE_MIN_SCORE = 3.0
READ_MIN_SCORE = 2.0
STRONG_WEIGHT = 2.0
WEAK_WEIGHT = 1.0

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class RoutingResult:
    skill: Skill | None
    score: float
    matched_terms: tuple[str, ...]
    # Populated when nothing scored high enough, to offer the reader a choice.
    alternatives: tuple[Skill, ...] = ()


def normalize(message: str) -> tuple[str, set[str]]:
    lowered = message.lower()
    return lowered, set(TOKEN_PATTERN.findall(lowered))


def score_skill(
    skill: Skill, lowered: str, tokens: set[str]
) -> tuple[float, list[str], bool]:
    """Return (score, matched terms, whether a strong trigger matched)."""
    score = 0.0
    matched: list[str] = []
    strong_hit = False
    for trigger in sorted(skill.strong_triggers):
        # Multi-word triggers are matched as phrases, single words as tokens.
        hit = trigger in lowered if " " in trigger else trigger in tokens
        if hit:
            score += STRONG_WEIGHT
            matched.append(trigger)
            strong_hit = True
    for trigger in sorted(skill.weak_triggers):
        hit = trigger in lowered if " " in trigger else trigger in tokens
        if hit:
            score += WEAK_WEIGHT
            matched.append(trigger)
    return score, matched, strong_hit


def route(message: str, role: Role) -> RoutingResult:
    """Select at most one skill. Ties and weak matches resolve to no skill."""
    lowered, tokens = normalize(message)
    scored: list[tuple[float, Skill, list[str], bool]] = []
    for skill in SKILLS:
        if role not in skill.allowed_roles:
            continue
        score, matched, strong_hit = score_skill(skill, lowered, tokens)
        if score > 0:
            scored.append((score, skill, matched, strong_hit))
    if not scored:
        return RoutingResult(None, 0.0, (), tuple(s for s in SKILLS if role in s.allowed_roles))

    # A strong trigger on a scheduling skill is an explicit imperative
    # ("regenerate", "recrawl", "scan"), so it breaks a tie against a read
    # skill that only matched shared topic vocabulary. Everything else keeps
    # the read skill, and a genuine tie stays ambiguous.
    scored.sort(
        key=lambda entry: (
            -entry[0],
            not (entry[1].effect is SkillEffect.SCHEDULE and entry[3]),
            entry[1].key,
        )
    )
    best_score, best_skill, matched, best_strong = scored[0]
    threshold = (
        SCHEDULE_MIN_SCORE if best_skill.effect is SkillEffect.SCHEDULE else READ_MIN_SCORE
    )
    if best_score < threshold:
        return RoutingResult(
            None, best_score, tuple(matched), tuple(entry[1] for entry in scored[:3])
        )
    if len(scored) > 1 and scored[1][0] == best_score:
        runner_up = scored[1]
        imperative_wins = (
            best_skill.effect is SkillEffect.SCHEDULE
            and best_strong
            and not (runner_up[1].effect is SkillEffect.SCHEDULE and runner_up[3])
        )
        if not imperative_wins:
            # An exact tie is ambiguous, not a coin flip.
            return RoutingResult(
                None, best_score, tuple(matched), tuple(entry[1] for entry in scored[:3])
            )
    return RoutingResult(best_skill, best_score, tuple(matched))
