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


def test_a_ga4_property_is_accepted_in_both_the_forms_a_person_has() -> None:
    """The GA4 endpoint used to take the Search Console schema.

    Every valid GA4 reference was refused with "invalid Search Console
    URL-prefix property", so the connector could not be authorized at all --
    route right, service right, and the schema between them unable to carry the
    argument. Both spellings normalise to the one the Admin API uses.
    """
    from app.api.schemas import AnalyticsAuthorizationCreate

    for given in ("properties/123456789", "123456789", "  properties/123456789  "):
        assert (
            AnalyticsAuthorizationCreate(property_ref=given).property_ref
            == "properties/123456789"
        )


def test_a_ga4_property_that_is_not_a_property_is_refused() -> None:
    from app.api.schemas import AnalyticsAuthorizationCreate

    for given in (
        "sc-domain:example.com",
        "https://example.com/",
        "properties/",
        "properties/abc",
        "properties/123/streams/4",
        "",
        "   ",
    ):
        with pytest.raises(ValidationError):
            AnalyticsAuthorizationCreate(property_ref=given)
