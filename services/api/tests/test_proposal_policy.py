"""Policy and validation rules for proposals.

These exercise `app.domain.proposals` directly, which is the single
implementation the API service calls. The worker previously carried a
byte-identical fork of this module that only its own tests imported; it was
removed so a rule can no longer be tightened in one copy and left open in the
other.
"""

from uuid import uuid4

import pytest

from app.domain.proposals import (
    detect_control_directive_changes,
    evaluate_proposal_policy,
    generate_unified_diff,
    resolve_required_approver_count,
    validate_proposal_content,
)


def policy_for(
    target_type: str,
    target_path: str,
    before: str,
    after: str,
    tenant_mode: str = "autopilot",
):
    checks = validate_proposal_content(target_type, target_path, before, after)
    return checks, evaluate_proposal_policy(
        target_type, target_path, before, after, checks, author_id=uuid4(), tenant_mode=tenant_mode
    )


def test_unified_diff_generation() -> None:
    diff = generate_unified_diff("line 1\nline 2\n", "line 1\nline 2 modified\nline 3\n", "example.html")
    assert "--- a/example.html" in diff
    assert "+++ b/example.html" in diff
    assert "-line 2" in diff
    assert "+line 2 modified" in diff
    assert "+line 3" in diff


def test_validator_blocks_empty_diff_and_invalid_json_ld() -> None:
    checks = validate_proposal_content("html_meta", "/page", "same", "same")
    assert any(c.name == "non_empty_diff" and not c.passed for c in checks)

    bad_json = validate_proposal_content("json_ld_schema", "/page", "{}", "{ not json }")
    assert any(c.name == "json_ld_syntax" and not c.passed for c in bad_json)

    good_json = validate_proposal_content(
        "json_ld_schema",
        "/page",
        "{}",
        '{"@context":"https://schema.org","@type":"Article","headline":"Test"}',
    )
    assert all(c.passed for c in good_json)


def test_validator_blocks_prohibited_guarantee_claims() -> None:
    checks = validate_proposal_content(
        "html_meta", "/page", "<title>Old</title>", "<title>Best Shoes - Guaranteed #1 ranking on Google</title>"
    )
    assert any(c.name == "prohibited_claim_safety" and not c.passed for c in checks)


def test_policy_assigns_low_risk_for_meta_and_medium_for_body_content() -> None:
    _, meta = policy_for("html_meta", "/page", "<title>A</title>", "<title>A better title</title>")
    assert meta.risk == "low"
    assert meta.required_approver_count == 1

    _, content = policy_for(
        "content_edit", "/page", "short old", "much longer new content modification exceeding limit " * 10
    )
    assert content.risk in {"medium", "high"}
    assert content.required_approver_count == 2


@pytest.mark.parametrize(
    ("directive", "before", "after"),
    [
        (
            "canonical",
            '<link rel="canonical" href="https://codearc.net/"/>',
            '<link rel="canonical" href="https://attacker.example/"/>',
        ),
        (
            "meta_robots",
            '<meta name="robots" content="index,follow">',
            '<meta name="robots" content="noindex,nofollow">',
        ),
        (
            "meta_refresh",
            "<title>Old</title>",
            '<meta http-equiv="refresh" content="0;url=https://attacker.example/">',
        ),
        (
            "x_robots_tag",
            "Cache-Control: max-age=60",
            "X-Robots-Tag: noindex",
        ),
    ],
)
def test_indexing_control_changes_are_high_risk_and_never_auto_deploy(
    directive: str, before: str, after: str
) -> None:
    """A directive change is high risk however small the diff is.

    `html_meta` is otherwise the cheapest change class, so without a
    content-aware rule a noindex or a canonical pointed at another domain would
    take one approver, admit the editor role, and auto-deploy under autopilot.
    """
    checks, decision = policy_for("html_meta", "/pricing", before, after)
    assert all(c.passed for c in checks), "the payload itself must be otherwise valid"
    assert decision.control_directive_changes == [directive]
    assert decision.risk == "high"
    assert decision.can_auto_deploy is False
    assert decision.required_approver_count == 2
    assert "editor" not in decision.allowed_roles


def test_removing_a_canonical_is_also_a_directive_change() -> None:
    changed = detect_control_directive_changes('<link rel="canonical" href="https://codearc.net/"/>', "")
    assert changed == ["canonical"]


def test_untouched_directive_does_not_escalate_an_ordinary_edit() -> None:
    canonical = '<link rel="canonical" href="https://codearc.net/pricing"/>'
    _, decision = policy_for(
        "html_meta", "/pricing", f"{canonical}<title>Old</title>", f"{canonical}<title>New</title>"
    )
    assert decision.control_directive_changes == []
    assert decision.risk == "low"


@pytest.mark.parametrize(
    "target_path",
    [
        "../../etc/passwd",
        "..\\..\\etc\\passwd",
        "%2e%2e/%2e%2e/etc/passwd",
        "%252e%252e/etc/passwd",
        "/etc/passwd",
    ],
)
def test_github_file_paths_reject_traversal_and_absolute_paths(target_path: str) -> None:
    checks = validate_proposal_content("github_file", target_path, "a", "b")
    assert any(c.name == "target_path_safety" and not c.passed for c in checks), target_path


def test_url_target_keeps_a_leading_slash_but_rejects_protocol_relative() -> None:
    ok = validate_proposal_content("html_meta", "/pricing", "a", "b")
    assert all(c.passed for c in ok)

    bad = validate_proposal_content("html_meta", "//attacker.example/pricing", "a", "b")
    assert any(c.name == "target_path_safety" and not c.passed for c in bad)


def test_prohibited_claim_requires_two_approvers_if_it_is_ever_reconsidered() -> None:
    _, decision = policy_for("html_meta", "/page", "<title>Old</title>", "<title>Guaranteed ranking</title>")
    assert decision.risk == "prohibited"
    assert decision.can_auto_deploy is False
    assert decision.required_approver_count == 2


def test_a_site_may_lower_the_approver_count_for_a_medium_change() -> None:
    """A small site should not need three people to change a heading."""
    assert resolve_required_approver_count("medium", None) == 2
    assert resolve_required_approver_count("medium", 1) == 1
    assert resolve_required_approver_count("low", 1) == 1


def test_a_site_may_raise_the_approver_count_on_any_tier() -> None:
    assert resolve_required_approver_count("low", 3) == 3
    assert resolve_required_approver_count("medium", 4) == 4
    assert resolve_required_approver_count("high", 5) == 5


def test_no_site_setting_can_lower_a_high_risk_change_below_two() -> None:
    """Canonical, robots and redirect edits are forced high precisely so that
    no per-site setting can reach them."""
    assert resolve_required_approver_count("high", 1) == 2
    assert resolve_required_approver_count("prohibited", 1) == 2


def test_the_count_can_never_reach_zero() -> None:
    """A change nobody approves is Autopilot, which has its own controls."""
    for risk in ("low", "medium", "high", "prohibited"):
        assert resolve_required_approver_count(risk, 0) >= 1


def test_an_unknown_risk_label_falls_back_to_two_approvers() -> None:
    assert resolve_required_approver_count("something-new", None) == 2
    assert resolve_required_approver_count("something-new", 1) == 2


def test_the_site_setting_reaches_the_policy_decision() -> None:
    decision = evaluate_proposal_policy(
        "github_file",
        "CalcHive/emi-calculator.html",
        "<html><h1>old</h1></html>",
        "<html><h1>new</h1></html>",
        [],
        site_required_approver_count=1,
    )
    assert decision.risk == "medium"
    assert decision.required_approver_count == 1

    default = evaluate_proposal_policy(
        "github_file",
        "CalcHive/emi-calculator.html",
        "<html><h1>old</h1></html>",
        "<html><h1>new</h1></html>",
        [],
    )
    assert default.required_approver_count == 2


def test_a_site_setting_cannot_soften_an_indexing_control_change() -> None:
    decision = evaluate_proposal_policy(
        "github_file",
        "CalcHive/emi-calculator.html",
        '<html><link rel="canonical" href="https://a.example/x"></html>',
        '<html><link rel="canonical" href="https://b.example/y"></html>',
        [],
        site_required_approver_count=1,
    )
    assert decision.risk == "high"
    assert decision.required_approver_count == 2
    assert decision.can_auto_deploy is False
