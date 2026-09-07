from typing import Any

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr, ValidationError

from app.core.auth import resolve_local_pilot_context
from app.core.config import LOCAL_PILOT_ACTOR_ID, Settings
from app.core.context import Role


def pilot_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "development",
        "cursor_signing_key": "c" * 32,
        "local_pilot_auth_enabled": True,
        "local_pilot_auth_token": SecretStr("p" * 32),
    }
    for key, value in overrides.items():
        values[key] = SecretStr(value) if key.endswith("_token") and isinstance(value, str) else value
    return Settings(**values)  # pyright: ignore[reportCallIssue]


def credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_local_pilot_requires_exact_bearer_token() -> None:
    settings = pilot_settings()
    assert resolve_local_pilot_context(settings, None) is None
    assert (
        resolve_local_pilot_context(
            settings,
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong"),
        )
        is None
    )
    context = resolve_local_pilot_context(
        settings,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="p" * 32),
    )
    assert context is not None
    assert context.tenant_id == settings.local_pilot_tenant_id
    assert context.actor_id == settings.local_pilot_actor_id
    assert context.role == Role.OWNER


def test_local_pilot_is_rejected_outside_development() -> None:
    with pytest.raises(ValidationError, match="development-only"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="production",
            cursor_signing_key="c" * 32,
            # A configured provider, so this asserts the pilot-token rule rather
            # than the one that requires an issuer outside development.
            oidc_issuer_url="https://issuer.example.com",
            oidc_audience="seo-autopilot",
            local_pilot_auth_enabled=True,
            local_pilot_auth_token=SecretStr("p" * 32),
        )


def test_a_second_operator_token_resolves_to_a_different_actor() -> None:
    """Separation of duties needs two identities to be exercisable at all.

    With one token the author of a proposal can never be joined by an approver,
    so a medium-risk change is refused forever and approved never.
    """
    settings = pilot_settings(
        local_pilot_auth_token="operator-token-that-is-long-enough-32",
        local_pilot_reviewer_token="reviewer-token-that-is-long-enough-32",
    )

    operator = resolve_local_pilot_context(
        settings, credentials("operator-token-that-is-long-enough-32")
    )
    reviewer = resolve_local_pilot_context(
        settings, credentials("reviewer-token-that-is-long-enough-32")
    )

    assert operator is not None and reviewer is not None
    assert operator.actor_id == settings.local_pilot_actor_id
    assert reviewer.actor_id == settings.local_pilot_reviewer_id
    assert operator.actor_id != reviewer.actor_id
    assert operator.tenant_id == reviewer.tenant_id


def test_an_unknown_token_is_still_nobody_when_two_operators_exist() -> None:
    settings = pilot_settings(
        local_pilot_auth_token="operator-token-that-is-long-enough-32",
        local_pilot_reviewer_token="reviewer-token-that-is-long-enough-32",
    )
    assert resolve_local_pilot_context(settings, credentials("neither-of-those-tokens-at-all-32")) is None


def test_the_reviewer_token_may_not_equal_the_operator_token() -> None:
    """One secret behind two actor ids would let one holder be both approvers."""
    with pytest.raises(ValidationError, match="reviewer token must differ"):
        pilot_settings(
            local_pilot_auth_token="the-very-same-token-long-enough-32ch",
            local_pilot_reviewer_token="the-very-same-token-long-enough-32ch",
        )


def test_the_reviewer_must_be_a_different_actor() -> None:
    with pytest.raises(ValidationError, match="different actor"):
        pilot_settings(
            local_pilot_auth_token="operator-token-that-is-long-enough-32",
            local_pilot_reviewer_token="reviewer-token-that-is-long-enough-32",
            local_pilot_reviewer_id=LOCAL_PILOT_ACTOR_ID,
        )


def test_a_short_reviewer_token_is_refused() -> None:
    with pytest.raises(ValidationError, match="LOCAL_PILOT_REVIEWER_TOKEN"):
        pilot_settings(
            local_pilot_auth_token="operator-token-that-is-long-enough-32",
            local_pilot_reviewer_token="too-short",
        )
