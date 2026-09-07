"""What the app refuses, and no longer refuses, outside development.

Production mode was unreachable rather than hardened. Two validators between
them left no configuration that could start: connectors outside development
demanded a `managed` secret backend, and nothing implements one, while every
connector route serves only `database_envelope`. So the pilot ran with
`APP_ENV=development` on a public host, which is a far worse outcome than the
rule was trying to prevent.

These pin the shape that replaces it: the envelope backend is a supported
production configuration as long as its key is supplied, and asking for the
backend that does not exist fails at startup instead of at the first tenant who
tries to connect something.
"""

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings

ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "production",
        "cursor_signing_key": "c" * 32,
        "oidc_issuer_url": "https://issuer.example.com",
        "oidc_audience": "seo-autopilot",
        "google_connectors_enabled": True,
        "google_client_id": "client-id",
        "google_client_secret": SecretStr("client-secret"),
        "search_query_hash_key": SecretStr("q" * 32),
        "connector_secret_backend": "database_envelope",
        "connector_secret_encryption_key": SecretStr(ENCRYPTION_KEY),
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportCallIssue]


def test_production_runs_connectors_on_the_envelope_backend() -> None:
    configured = settings()
    assert configured.app_env == "production"
    assert configured.connector_secret_backend == "database_envelope"


def test_production_still_requires_the_envelope_key() -> None:
    with pytest.raises(ValidationError, match="CONNECTOR_SECRET_ENCRYPTION_KEY"):
        settings(connector_secret_encryption_key=None)


def test_the_managed_backend_is_refused_because_nothing_implements_it() -> None:
    """Named honestly rather than accepted and quietly unserved.

    Every connector route checks for `database_envelope` and returns 503 for
    anything else, so a deployment that selected `managed` would start, look
    configured, and refuse every connector with no explanation.
    """
    with pytest.raises(ValidationError, match="not implemented"):
        settings(connector_secret_backend="managed")


def test_the_managed_backend_is_refused_in_development_too() -> None:
    with pytest.raises(ValidationError, match="not implemented"):
        settings(app_env="development", connector_secret_backend="managed")
