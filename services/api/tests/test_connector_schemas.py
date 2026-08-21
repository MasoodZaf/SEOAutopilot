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


def test_production_rejects_local_database_secret_backend() -> None:
    with pytest.raises(ValidationError, match="managed connector secret backend"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="production",
            cursor_signing_key="c" * 32,
            google_connectors_enabled=True,
            google_client_id="client",
            google_client_secret=SecretStr("secret"),
            search_query_hash_key=SecretStr("q" * 32),
            connector_secret_backend="database_envelope",
            connector_secret_encryption_key=SecretStr(
                "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="
            ),
        )
