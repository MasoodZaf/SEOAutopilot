from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr, ValidationError

from app.api.schemas import ConnectorSyncCreate
from app.core.config import Settings


def test_google_connector_remains_closed_without_credentials() -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        cursor_signing_key="c" * 32,
        google_connectors_enabled=False,
    )
    assert settings.google_connectors_enabled is False


def test_dns_provider_connector_requires_a_secret_backend_when_enabled() -> None:
    with pytest.raises(ValidationError, match="connector secret backend"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="test",
            cursor_signing_key="c" * 32,
            dns_provider_connectors_enabled=True,
        )


def test_enabling_google_requires_credentials_and_separate_hash_key() -> None:
    with pytest.raises(ValidationError, match="Google connector credentials"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="test",
            cursor_signing_key="c" * 32,
            google_connectors_enabled=True,
        )
    with pytest.raises(ValidationError, match="SEARCH_QUERY_HASH_KEY"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="test",
            cursor_signing_key="c" * 32,
            google_connectors_enabled=True,
            google_client_id="client",
            google_client_secret=SecretStr("secret"),
        )


def test_sync_rejects_future_end_date() -> None:
    with pytest.raises(ValidationError, match="range_end must not be in the future"):
        ConnectorSyncCreate(
            range_start=datetime.now(UTC).date(),
            range_end=datetime.now(UTC).date() + timedelta(days=1),
        )


def test_production_refuses_the_backend_that_does_not_exist() -> None:
    """Production used to *require* `managed`, which nothing implements.

    The rule read as a hardening measure and worked as a prohibition on running
    in production at all, so the pilot ran with APP_ENV=development on a public
    host. The envelope backend is now a supported production configuration and
    asking for the one that was never built fails here instead.
    """
    with pytest.raises(ValidationError, match="not implemented"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="production",
            cursor_signing_key="c" * 32,
            oidc_issuer_url="https://issuer.example.com",
            oidc_audience="seo-autopilot",
            google_connectors_enabled=True,
            google_client_id="client",
            google_client_secret=SecretStr("secret"),
            search_query_hash_key=SecretStr("q" * 32),
            connector_secret_backend="managed",
        )
