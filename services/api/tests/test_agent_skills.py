import pytest

from app.core.context import Role
from app.domain.skills import (
    READ_MIN_SCORE,
    SCHEDULE_MIN_SCORE,
    SKILLS,
    SKILLS_BY_KEY,
    SkillEffect,
    route,
)


def routed(message: str, role: Role = Role.OWNER) -> str | None:
    result = route(message, role)
    return result.skill.key if result.skill else None


def test_each_intent_routes_to_its_own_skill() -> None:
    assert routed("Audit the site and show the top issues") == "site_audit"
    assert routed("What keyword opportunities do we have?") == "keyword_research"
    assert routed("How is our sitemap coverage?") == "sitemap_review"
    assert routed("How do we compare to competitors?") == "competitor_pages"
    assert routed("What is our AI search visibility?") == "ai_visibility"
    assert routed("Show me the latest weekly report") == "weekly_report"
    assert routed("What routines are scheduled?") == "routines"
    assert routed("What content briefs are queued?") == "content_briefs"


def test_scheduling_requires_a_clearly_imperative_request() -> None:
    assert routed("Run a new crawl of the site") == "run_site_audit"
    assert routed("scan the competitors now") == "run_competitor_scan"
    assert routed("recluster our keywords") == "run_keyword_research"
    # Scheduling work is held to a higher bar than answering a question.
    assert SCHEDULE_MIN_SCORE > READ_MIN_SCORE


def test_an_unclear_request_starts_nothing_and_offers_choices() -> None:
    for vague in ("hello there", "make my site rank number one tomorrow", "help"):
        result = route(vague, Role.OWNER)
        assert result.skill is None
        assert result.alternatives


def test_an_explicit_imperative_breaks_a_tie_toward_doing_the_work() -> None:
    """"Regenerate" ties on score with "show me the briefs", but says which one."""
    assert routed("regenerate the content briefs") == "run_content_briefs"
    assert routed("rebuild the briefs") == "run_content_briefs"
    # Without the imperative the same topic words read as a question.
    assert routed("what content briefs do we have") == "content_briefs"


def test_a_tie_with_no_imperative_stays_ambiguous_rather_than_guessed() -> None:
    result = route("keyword briefs", Role.OWNER)
    assert result.skill is None
    assert {item.key for item in result.alternatives} >= {"content_briefs", "keyword_research"}


def test_an_imperative_never_promotes_a_skill_the_role_cannot_invoke() -> None:
    # An editor may regenerate briefs; a viewer may not, and the imperative
    # tie-break must not hand them the scheduling skill instead.
    assert routed("regenerate the content briefs", Role.EDITOR) == "run_content_briefs"
    assert routed("regenerate the content briefs", Role.VIEWER) == "content_briefs"


def test_a_role_can_never_reach_a_skill_it_is_not_allowed() -> None:
    # The same words that schedule a crawl for an owner route nowhere for a viewer.
    assert routed("Run a new crawl of the site", Role.OWNER) == "run_site_audit"
    assert routed("Run a new crawl of the site", Role.VIEWER) is None
    for role in Role:
        for candidate in SKILLS:
            if role not in candidate.allowed_roles:
                result = route(candidate.example, role)
                assert result.skill is None or result.skill.key != candidate.key


def test_no_skill_can_deploy_or_approve() -> None:
    """The registry is the whole surface; nothing in it touches the change path."""
    for skill in SKILLS:
        assert skill.effect in {SkillEffect.READ, SkillEffect.SCHEDULE}
        if skill.effect is SkillEffect.SCHEDULE:
            # A scheduling skill can only queue a routine kind, and routines
            # hold no deployment authority.
            assert skill.routine_kind in {
                "site_audit",
                "keyword_refresh",
                "sitemap_coverage",
                "content_briefs",
                "competitor_scan",
                "ai_visibility_scan",
                "weekly_report",
            }
        else:
            assert skill.routine_kind is None


def test_routing_is_deterministic_and_case_insensitive() -> None:
    assert routed("SHOW ME THE LATEST WEEKLY REPORT") == "weekly_report"
    assert routed("Show me the latest weekly report") == "weekly_report"
    first = route("what keyword clusters do we have", Role.OWNER)
    second = route("what keyword clusters do we have", Role.OWNER)
    assert (first.skill, first.score) == (second.skill, second.score)


def test_registry_keys_are_unique_and_examples_route_to_themselves() -> None:
    assert len(SKILLS_BY_KEY) == len(SKILLS)
    unroutable = [
        skill.key
        for skill in SKILLS
        if route(skill.example, Role.OWNER).skill is not skill
    ]
    # Every advertised example must actually reach the skill it advertises.
    assert unroutable == []


@pytest.mark.parametrize("skill", SKILLS, ids=lambda item: item.key)
def test_every_skill_is_described_for_a_reader(skill) -> None:
    assert skill.name and skill.description and skill.example
    assert skill.strong_triggers or skill.weak_triggers
