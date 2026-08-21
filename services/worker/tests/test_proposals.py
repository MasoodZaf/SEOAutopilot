from uuid import uuid4

from app.proposals.diff import generate_unified_diff
from app.proposals.policy import evaluate_proposal_policy
from app.proposals.validator import validate_proposal_content


def test_unified_diff_generation() -> None:
    before = "line 1\nline 2\n"
    after = "line 1\nline 2 modified\nline 3\n"
    diff = generate_unified_diff(before, after, "example.html")
    assert "--- a/example.html" in diff
    assert "+++ b/example.html" in diff
    assert "-line 2" in diff
    assert "+line 2 modified" in diff
    assert "+line 3" in diff


def test_validator_blocks_empty_diff_and_invalid_json_ld() -> None:
    # Empty diff
    checks = validate_proposal_content("html_meta", "/page", "same", "same")
    assert any(c.name == "non_empty_diff" and not c.passed for c in checks)

    # Invalid JSON-LD
    bad_json = validate_proposal_content("json_ld_schema", "/page", "{}", "{ not json }")
    assert any(c.name == "json_ld_syntax" and not c.passed for c in bad_json)

    # Valid JSON-LD
    good_json = validate_proposal_content(
        "json_ld_schema",
        "/page",
        "{}",
        '{"@context":"https://schema.org","@type":"Article","headline":"Test"}',
    )
    assert all(c.passed for c in good_json)


def test_validator_blocks_prohibited_guarantee_claims() -> None:
    prohibited_content = "<title>Best Shoes - Guaranteed #1 ranking on Google</title>"
    checks = validate_proposal_content("html_meta", "/page", "<title>Old</title>", prohibited_content)
    assert any(c.name == "prohibited_claim_safety" and not c.passed for c in checks)


def test_policy_evaluator_assigns_low_risk_for_meta_and_medium_for_body_content() -> None:
    val_ok = validate_proposal_content("html_meta", "/page", "old", "new")
    policy_meta = evaluate_proposal_policy("html_meta", "/page", "old", "new", val_ok, author_id=uuid4())
    assert policy_meta.risk == "low"
    assert policy_meta.required_approver_count == 1

    policy_content = evaluate_proposal_policy(
        "content_edit", "/page", "short old", "much longer new content modification exceeding limit " * 10, val_ok, author_id=uuid4()
    )
    assert policy_content.risk in {"medium", "high"}
    assert policy_content.required_approver_count == 2
