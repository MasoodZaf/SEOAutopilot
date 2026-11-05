import pytest

from app.domain.proposals import evaluate_proposal_policy, validate_proposal_content
from app.domain.publishing import publish_blocked_reason
from app.domain.repo_paths import path_kind


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (".github/workflows/deploy.yml", "ci"),
        (".github/actions/x/action.yml", "ci"),
        (".gitlab-ci.yml", "ci"),
        (".circleci/config.yml", "ci"),
        (".github/CODEOWNERS", "configuration"),
        ("package.json", "configuration"),
        ("next.config.ts", "configuration"),
        ("robots.txt", "indexing"),
        ("_layouts/default.html", "template"),
        ("blog/how-emi-works.html", None),
        ("emi-calculator.html", None),
    ],
)
def test_each_path_is_named_for_what_it_controls(path, kind):
    assert path_kind(path) == kind


def test_a_workflow_file_is_refused_before_any_branch_is_pushed():
    """Pushing a workflow to any branch runs it with the repository's secrets."""
    checks = validate_proposal_content("github_file", ".github/workflows/seo.yml", "", "on: push")
    path_check = next(check for check in checks if check.name == "target_path_safety")
    assert not path_check.passed
    decision = evaluate_proposal_policy("github_file", ".github/workflows/seo.yml", "a", "b", checks)
    assert decision.rejection_reasons


@pytest.mark.parametrize("path", ["package.json", "_includes/header.html", "netlify.toml"])
def test_a_small_edit_to_a_file_every_page_depends_on_is_high_risk(path):
    checks = validate_proposal_content("github_file", path, "abc", "abd")
    assert evaluate_proposal_policy("github_file", path, "abc", "abd", checks).risk == "high"


def test_an_ordinary_page_edit_keeps_its_size_based_tier():
    checks = validate_proposal_content("github_file", "emi-calculator.html", "abc", "abd")
    assert evaluate_proposal_policy("github_file", "emi-calculator.html", "abc", "abd", checks).risk == "medium"


def test_publishing_still_refuses_what_it_refused():
    assert publish_blocked_reason(".github/workflows/x.yml", "", "") == "site_configuration_file"
    assert publish_blocked_reason("robots.txt", "", "") == "indexing_control_file"
    assert publish_blocked_reason("_layouts/base.html", "", "") == "template_wide_file"
    assert publish_blocked_reason("blog/post.html", "", "x") is None
