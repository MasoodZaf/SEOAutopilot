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
    # Words that ask for the work to actually happen. A scheduling skill needs
    # one of these before it is even a candidate: naming a topic is a question,
    # not an instruction, so "how is our sitemap coverage?" must never start a
    # scan.
    action_triggers: frozenset[str] = field(default_factory=frozenset)
    # Routine kind queued when effect is SCHEDULE.
    routine_kind: str | None = None
    example: str = ""


# Words that ask for work to happen, shared by every scheduling skill. They
# cannot pick a subject on their own, so each scheduling skill also carries its
# own topic terms and must clear SCHEDULE_MIN_SCORE on the combination.
# "weekly" is deliberately absent: it is part of the weekly report's own name,
# so treating it as an action would turn "show me the weekly report" into a
# request to run one.
ACTION_TRIGGERS = frozenset(
    {"schedule", "scheduled", "every", "each", "daily", "monthly", "run", "start"}
)

ALL_ROLES = frozenset(Role)
ACTING_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.SEO_MANAGER})
CONTENT_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR})

def scheduling(
    key: str,
    name: str,
    description: str,
    routine_kind: str,
    topic: set[str],
    extra: set[str],
    roles: frozenset[Role],
    example: str,
) -> Skill:
    """A scheduling skill scores on its subject plus any scheduling verb."""
    return Skill(
        key=key,
        name=name,
        description=description,
        effect=SkillEffect.SCHEDULE,
        allowed_roles=roles,
        strong_triggers=frozenset(topic),
        action_triggers=frozenset(extra | ACTION_TRIGGERS),
        routine_kind=routine_kind,
        example=example,
    )


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
    scheduling(
        "run_site_audit", "Crawl and audit",
        "Crawl the site so the next audit reads current evidence.",
        "site_audit", {"audit", "crawl"}, {"recrawl", "rescan"}, ACTING_ROLES,
        "Run a crawl and audit every day.",
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
        strong_triggers=frozenset({"keyword", "keywords", "cluster", "clusters", "queries"}),
        weak_triggers=frozenset({"search", "demand", "ranking", "terms", "topics", "intent"}),
        example="What keyword opportunities do we have?",
    ),
    scheduling(
        "run_keyword_research", "Recluster keyword demand",
        "Rebuild keyword clusters from the most recent search evidence.",
        "keyword_refresh", {"keyword", "keywords", "cluster", "clusters"},
        {"recluster", "recompute"}, ACTING_ROLES,
        "Recluster our keywords every week.",
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
        strong_triggers=frozenset({"brief", "briefs", "refresh queue", "content plan"}),
        weak_triggers=frozenset({"content", "queue", "plan", "write", "rewrite", "page"}),
        example="What content briefs are queued?",
    ),
    scheduling(
        "run_content_briefs", "Regenerate content briefs",
        "Rebuild briefs from the latest keyword clusters and page evidence.",
        "content_briefs", {"brief", "briefs"}, {"regenerate", "rebuild"}, CONTENT_ROLES,
        "Regenerate the content briefs every week.",
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
    scheduling(
        "run_sitemap_coverage", "Recheck sitemap coverage",
        "Recompute declared-versus-crawled-versus-indexable from the latest crawl.",
        "sitemap_coverage", {"sitemap", "sitemaps", "coverage"}, {"recheck"}, ACTING_ROLES,
        "Check sitemap coverage every week.",
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
    scheduling(
        "run_competitor_scan", "Scan tracked competitors",
        "Refetch the competitor pages already on record. No new URLs are discovered.",
        "competitor_scan", {"competitor", "competitors"}, {"scan", "refetch", "monitor"},
        ACTING_ROLES, "Scan the competitors we track every week.",
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
        strong_triggers=frozenset(
            {"visibility", "answer engine", "aeo", "geo", "llm", "chatgpt"}
        ),
        weak_triggers=frozenset({"ai", "citation", "citations", "readiness"}),
        example="What is our AI search visibility readiness?",
    ),
    scheduling(
        "run_ai_visibility_scan", "Recheck answer-engine readiness",
        "Recompute answer-engine readiness from the latest crawl and search evidence.",
        "ai_visibility_scan", {"visibility", "answer engine", "aeo"},
        {"recheck"}, ACTING_ROLES,
        "Check our AI search visibility every week.",
    ),
    Skill(
        key="weekly_report",
        name="Weekly report",
        description="Show the latest weekly digest: what changed, what opened, what resolved.",
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"report", "digest", "summary"}),
        weak_triggers=frozenset({"week", "weekly", "changed", "progress", "update"}),
        example="Show me the latest weekly report.",
    ),
    scheduling(
        "run_weekly_report", "Send the weekly report",
        "Generate the weekly digest, and deliver it to any configured channel.",
        "weekly_report", {"report", "digest"}, {"send"}, ACTING_ROLES,
        "Send me the weekly report every Monday.",
    ),
    Skill(
        key="routines",
        name="Scheduled routines",
        description="Show which routines are scheduled, when they next run, and how the last run ended.",
        effect=SkillEffect.READ,
        allowed_roles=ALL_ROLES,
        strong_triggers=frozenset({"routine", "routines", "cron", "automation"}),
        weak_triggers=frozenset({"schedule", "scheduled", "recurring", "next", "when"}),
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


WEEKDAYS = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
    "friday": 5, "saturday": 6, "sunday": 7,
}

# Phrases that ask for a repeating schedule rather than a single run.
DAILY_PHRASES = ("daily", "every day", "each day", "every morning", "each morning")
# "weekly" alone is excluded: it is part of the weekly report's own name, so it
# would turn "run the weekly report" into a recurring schedule nobody asked for.
WEEKLY_PHRASES = ("every week", "each week")
MONTHLY_PHRASES = ("monthly", "every month", "each month")


@dataclass(frozen=True, slots=True)
class RequestedCadence:
    cadence: str
    isodow: int | None = None
    dom: int | None = None


def parse_cadence(message: str) -> RequestedCadence | None:
    """Read a repeating cadence out of the request, if one was asked for.

    Absent a cadence phrase the caller queues a single run, so "run an audit"
    never quietly becomes a recurring schedule.
    """
    lowered = message.lower()
    for day, isodow in WEEKDAYS.items():
        if f"every {day}" in lowered or f"each {day}" in lowered:
            return RequestedCadence("weekly", isodow=isodow)
    if any(phrase in lowered for phrase in MONTHLY_PHRASES):
        return RequestedCadence("monthly", dom=1)
    if any(phrase in lowered for phrase in WEEKLY_PHRASES):
        # Default to Monday when a weekday is not named.
        return RequestedCadence("weekly", isodow=1)
    if any(phrase in lowered for phrase in DAILY_PHRASES):
        return RequestedCadence("daily")
    return None


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


@dataclass(frozen=True, slots=True)
class SkillScore:
    score: float
    matched: tuple[str, ...]
    strong_hit: bool
    action_hit: bool


def score_skill(skill: Skill, lowered: str, tokens: set[str]) -> SkillScore:
    def hits(trigger: str) -> bool:
        # Multi-word triggers are matched as phrases, single words as tokens.
        return trigger in lowered if " " in trigger else trigger in tokens

    score = 0.0
    matched: list[str] = []
    strong_hit = False
    action_hit = False
    for trigger in sorted(skill.strong_triggers):
        if hits(trigger):
            score += STRONG_WEIGHT
            matched.append(trigger)
            strong_hit = True
    for trigger in sorted(skill.action_triggers):
        if hits(trigger):
            score += STRONG_WEIGHT
            matched.append(trigger)
            action_hit = True
    for trigger in sorted(skill.weak_triggers):
        if hits(trigger):
            score += WEAK_WEIGHT
            matched.append(trigger)
    return SkillScore(score, tuple(matched), strong_hit, action_hit)


def route(message: str, role: Role) -> RoutingResult:
    """Select at most one skill. Ties and weak matches resolve to no skill."""
    lowered, tokens = normalize(message)
    scored: list[tuple[float, Skill, tuple[str, ...], bool]] = []
    for skill in SKILLS:
        if role not in skill.allowed_roles:
            continue
        result = score_skill(skill, lowered, tokens)
        scheduling_skill = skill.effect is SkillEffect.SCHEDULE
        # Naming a topic is a question. A scheduling skill additionally needs a
        # word asking for the work to happen, and must clear the higher bar.
        if scheduling_skill and not (result.action_hit and result.strong_hit):
            continue
        threshold = SCHEDULE_MIN_SCORE if scheduling_skill else READ_MIN_SCORE
        if result.score >= threshold:
            scored.append((result.score, skill, result.matched, result.strong_hit))

    if not scored:
        near = sorted(
            (
                (score_skill(skill, lowered, tokens).score, skill)
                for skill in SKILLS
                if role in skill.allowed_roles
            ),
            key=lambda entry: (-entry[0], entry[1].key),
        )
        suggestions = tuple(skill for score, skill in near if score > 0)[:3]
        return RoutingResult(
            None,
            near[0][0] if near else 0.0,
            (),
            suggestions or tuple(s for s in SKILLS if role in s.allowed_roles)[:4],
        )

    # An explicit imperative on a scheduling skill breaks a tie against a read
    # skill that only matched shared topic vocabulary.
    scored.sort(
        key=lambda entry: (
            -entry[0],
            not (entry[1].effect is SkillEffect.SCHEDULE and entry[3]),
            entry[1].key,
        )
    )
    best_score, best_skill, matched, best_strong = scored[0]
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
                None, best_score, matched, tuple(entry[1] for entry in scored[:3])
            )
    return RoutingResult(best_skill, best_score, matched)
