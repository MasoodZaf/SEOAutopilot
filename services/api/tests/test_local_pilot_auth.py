import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr, ValidationError

from app.core.auth import resolve_local_pilot_context
from app.core.config import Settings
from app.core.context import Role


def pilot_settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="development",
        cursor_signing_key="c" * 32,
        local_pilot_auth_enabled=True,
        local_pilot_auth_token=SecretStr("p" * 32),
    )


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
            local_pilot_auth_enabled=True,
            local_pilot_auth_token=SecretStr("p" * 32),
        )
