"""What a repository reference and a path template are allowed to be.

The template is interpolated into a request path against GitHub's contents API,
and the repository reference into the same. Both come from a form, so both are
validated here rather than at deploy time, where the failure would land on a
change that had already been approved.
"""

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.github_connector import (
    GitHubConnectorError,
    GitHubRepositoryHttpProbe,
    github_settings_target,
    normalize_path_template,
    parse_repository,
)


def config(**overrides: object) -> Settings:
    values: dict[str, object] = {"app_env": "development", "cursor_signing_key": "x" * 32}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def probe(handler) -> GitHubRepositoryHttpProbe:
    return GitHubRepositoryHttpProbe(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_a_repository_reference_is_owner_and_name_and_nothing_else() -> None:
    assert parse_repository("MasoodZaf/mindTools") == ("MasoodZaf", "mindTools")
    assert parse_repository("  MasoodZaf/mindTools  ") == ("MasoodZaf", "mindTools")

    for raw in ("", "   ", "mindTools", "/mindTools", "MasoodZaf/", "a/b/c"):
        assert parse_repository(raw) is None


def test_a_reference_that_could_redirect_the_request_is_refused() -> None:
    """The slug is interpolated into a URL, so it must not be able to steer it.

    A query, a fragment, or an authority section smuggled through the form
    would send an authenticated request somewhere other than the repository the
    tenant named.
    """
    for raw in (
        "MasoodZaf/mindTools?ref=x",
        "MasoodZaf/mindTools#f",
        "evil.example@github/repo",
        "https://evil.example/repo",
        "MasoodZaf/mind Tools",
    ):
        assert parse_repository(raw) is None


def test_a_path_template_must_use_the_page_it_is_given() -> None:
    assert normalize_path_template("CalcHive/{path}.html") == "CalcHive/{path}.html"

    with pytest.raises(ValueError, match="path_template_missing_path"):
        # Every page would resolve to the same file, so the second deployment
        # would overwrite the first one's change.
        normalize_path_template("CalcHive/index.html")


def test_a_path_template_cannot_leave_the_repository() -> None:
    for raw in ("/etc/{path}", "../{path}.html", "a\\{path}", "{path}/../../x"):
        with pytest.raises(ValueError, match="path_template_unsafe"):
            normalize_path_template(raw)


def test_the_install_wide_target_is_only_parsed_from_a_usable_reference() -> None:
    target = github_settings_target(config(github_repository="MasoodZaf/mindTools"))
    assert target is not None
    assert target.slug == "MasoodZaf/mindTools"

    for raw in ("", "mindTools", "a/b/c"):
        assert github_settings_target(config(github_repository=raw)) is None


@pytest.mark.asyncio
async def test_a_token_that_cannot_push_is_reported_as_such() -> None:
    """Read access drafts a proposal fine and deploys nothing.

    Storing it would produce a connector that reports healthy and fails on the
    first approved change, which is the worst moment to learn about it.
    """
    handler = lambda _: httpx.Response(
        200,
        json={
            "full_name": "MasoodZaf/mindTools",
            "default_branch": "main",
            "permissions": {"pull": True, "push": False},
        },
    )
    facts = await probe(handler).inspect("MasoodZaf/mindTools", "ghp_read_only")

    assert facts.can_push is False
    assert facts.default_branch == "main"


@pytest.mark.asyncio
async def test_a_writable_repository_reports_what_github_calls_it() -> None:
    handler = lambda request: httpx.Response(
        200,
        json={
            "full_name": "MasoodZaf/mindTools",
            "default_branch": "trunk",
            "permissions": {"push": True},
            "archived": False,
        },
    )
    facts = await probe(handler).inspect("masoodzaf/mindtools", "ghp_write")

    # GitHub's own casing wins, so the stored reference matches the one every
    # later request is built from.
    assert facts.full_name == "MasoodZaf/mindTools"
    assert facts.default_branch == "trunk"
    assert facts.can_push is True


@pytest.mark.asyncio
async def test_a_repository_the_credential_cannot_see_gives_one_answer() -> None:
    """403 and 404 must not be distinguishable to the caller.

    GitHub returns 404 for a private repository a token cannot read. Reporting
    "not found" versus "forbidden" would turn this endpoint into a way to test
    whether a private repository exists.
    """
    for code in (403, 404):
        with pytest.raises(GitHubConnectorError, match="github_repository_not_accessible"):
            await probe(lambda _, code=code: httpx.Response(code, json={})).inspect(
                "acme/private", "ghp_x"
            )


@pytest.mark.asyncio
async def test_an_archived_repository_is_reported() -> None:
    handler = lambda _: httpx.Response(
        200,
        json={
            "full_name": "acme/old",
            "default_branch": "main",
            "permissions": {"push": True},
            "archived": True,
        },
    )
    assert (await probe(handler).inspect("acme/old", "ghp_x")).archived is True


def test_a_half_configured_app_is_refused_at_startup() -> None:
    """The tenant must not reach a grant screen the callback cannot honour.

    Installing a GitHub App gives this service write access to somebody's
    repository. Discovering afterwards that the callback has no private key to
    complete with leaves the grant standing and nothing to show for it.
    """
    for partial in (
        {"github_app_id": "123456"},
        {"github_app_slug": "seo-autopilot"},
        {"github_app_id": "123456", "github_app_slug": "seo-autopilot"},
    ):
        with pytest.raises(ValidationError, match="configured together or not at all"):
            config(**partial)


def test_the_connector_does_not_require_an_app_registration() -> None:
    """A pilot on a stored token must be able to move to a per-site connector.

    Requiring an app here would mean the install-wide token stays in place
    until a registration exists, which is the exact delay that leaves the
    shared credential running.
    """
    settings = config(
        github_connectors_enabled=True,
        connector_secret_backend="database_envelope",
        connector_secret_encryption_key="a" * 43 + "=",
    )
    assert settings.github_connectors_enabled is True
    assert settings.github_app_configured is False


def test_the_install_wide_token_cannot_be_enabled_outside_development() -> None:
    for environment in ("staging", "production"):
        with pytest.raises(ValidationError, match="development-only"):
            config(
                app_env=environment,
                github_legacy_token_enabled=True,
                github_repository="MasoodZaf/mindTools",
                github_token="t" * 40,
            )
