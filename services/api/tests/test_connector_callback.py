from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.db.models import AuditEvent, Connector, ConnectorOauthState, OutboxEvent, Site
from app.services.connectors import (
    ANALYTICS_READONLY_SCOPE,
    GSC_READONLY_SCOPE,
    ConnectorOAuthCallbackService,
)
from app.services.google_analytics import AnalyticsProperty
from app.services.google_oauth import GoogleProperty, GoogleTokenGrant

TENANT_ID = UUID("019d0000-0000-7000-8000-000000000011")
ACTOR_ID = UUID("019d0000-0000-7000-8000-000000000031")
SITE_ID = UUID("019d0000-0000-7000-8000-000000000012")
CONNECTOR_ID = UUID("019d0000-0000-7000-8000-000000000061")
STATE_ID = UUID("019d0000-0000-7000-8000-000000000071")


class FakeProvider:
    def __init__(
        self,
        *,
        scopes: frozenset[str] = frozenset({GSC_READONLY_SCOPE}),
        properties: list[GoogleProperty] | None = None,
    ) -> None:
        self.grant = GoogleTokenGrant(
            access_token="access-token-must-stay-secret",
            refresh_token="refresh-token-must-stay-secret",
            scopes=scopes,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        self.properties = properties or [GoogleProperty("sc-domain:example.com", "siteOwner")]
        self.exchange_code = AsyncMock(return_value=self.grant)
        self.list_properties = AsyncMock(return_value=self.properties)


class FakeSecretStore:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    async def store(self, tenant_id, connector_id, provider, payload) -> str:
        assert tenant_id == TENANT_ID
        assert connector_id == CONNECTOR_ID
        assert provider == "google_search_console"
        self.payload = payload
        return "managed-secret://opaque-reference"


def callback_records() -> tuple[ConnectorOauthState, Connector, Site]:
    oauth_state = ConnectorOauthState(
        id=STATE_ID,
        tenant_id=TENANT_ID,
        site_id=SITE_ID,
        connector_id=CONNECTOR_ID,
        state_hash="a" * 64,
        requested_scopes=[GSC_READONLY_SCOPE],
        requested_property_ref="sc-domain:example.com",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        created_by=ACTOR_ID,
    )
    connector = Connector(
        id=CONNECTOR_ID,
        tenant_id=TENANT_ID,
        site_id=SITE_ID,
        type="google_search_console",
        status="pending_authorization",
        version=1,
    )
    site = Site(
        id=SITE_ID,
        tenant_id=TENANT_ID,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
        status="active",
        verified_at=datetime.now(UTC),
    )
    return oauth_state, connector, site


@pytest.mark.asyncio
async def test_callback_consumes_state_binds_property_and_never_audits_tokens() -> None:
    oauth_state, connector, site = callback_records()
    session = MagicMock()
    # The callback resolves the state once unscoped, adopts its tenant scope, then
    # re-reads the same row under that scope before consuming it.
    session.scalar = AsyncMock(side_effect=[oauth_state, oauth_state, connector, site])
    session.execute = AsyncMock()
    session.add_all = MagicMock()
    provider = FakeProvider()
    secrets = FakeSecretStore()

    result = await ConnectorOAuthCallbackService(session, provider, secrets).complete_authorization(
        "state-value-not-persisted-1234567890", "authorization-code", "trace"
    )

    assert result is connector
    assert oauth_state.consumed_at is not None
    assert connector.status == "active"
    assert connector.external_account_ref == "sc-domain:example.com"
    assert connector.secret_ref == "managed-secret://opaque-reference"
    assert connector.granted_scopes == [GSC_READONLY_SCOPE]
    assert secrets.payload is not None
    assert secrets.payload["refresh_token"] == "refresh-token-must-stay-secret"
    provider.exchange_code.assert_awaited_once_with("authorization-code")
    provider.list_properties.assert_awaited_once_with("access-token-must-stay-secret")

    staged = session.add_all.call_args.args[0]
    assert any(isinstance(item, AuditEvent) for item in staged)
    assert any(isinstance(item, OutboxEvent) for item in staged)
    serialized = repr([item.__dict__ for item in staged])
    assert "authorization-code" not in serialized
    assert "access-token-must-stay-secret" not in serialized
    assert "refresh-token-must-stay-secret" not in serialized


@pytest.mark.asyncio
async def test_callback_rejects_replayed_state_before_provider_call() -> None:
    oauth_state, _, _ = callback_records()
    oauth_state.consumed_at = datetime.now(UTC)
    session = MagicMock()
    session.scalar = AsyncMock(return_value=oauth_state)
    session.execute = AsyncMock()
    provider = FakeProvider()

    with pytest.raises(HTTPException) as captured:
        await ConnectorOAuthCallbackService(session, provider, FakeSecretStore()).complete_authorization(
            "replayed-state-value-123456789012", "code", "trace"
        )
    assert captured.value.detail == "oauth_state_invalid"
    provider.exchange_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_rejects_broader_scope_without_storing_secret() -> None:
    oauth_state, connector, site = callback_records()
    session = MagicMock()
    # The callback resolves the state once unscoped, adopts its tenant scope, then
    # re-reads the same row under that scope before consuming it.
    session.scalar = AsyncMock(side_effect=[oauth_state, oauth_state, connector, site])
    session.execute = AsyncMock()
    provider = FakeProvider(
        scopes=frozenset(
            {GSC_READONLY_SCOPE, "https://www.googleapis.com/auth/webmasters"}
        )
    )
    secrets = FakeSecretStore()

    with pytest.raises(HTTPException) as captured:
        await ConnectorOAuthCallbackService(session, provider, secrets).complete_authorization(
            "state-value-for-scope-test-123456", "code", "trace"
        )
    assert captured.value.detail == "oauth_scope_mismatch"
    assert secrets.payload is None
    assert oauth_state.consumed_at is None


@pytest.mark.asyncio
async def test_callback_rejects_unverified_or_unrequested_property() -> None:
    oauth_state, connector, site = callback_records()
    session = MagicMock()
    # The callback resolves the state once unscoped, adopts its tenant scope, then
    # re-reads the same row under that scope before consuming it.
    session.scalar = AsyncMock(side_effect=[oauth_state, oauth_state, connector, site])
    session.execute = AsyncMock()
    provider = FakeProvider(
        properties=[GoogleProperty("sc-domain:example.com", "siteUnverifiedUser")]
    )

    with pytest.raises(HTTPException) as captured:
        await ConnectorOAuthCallbackService(session, provider, FakeSecretStore()).complete_authorization(
            "state-value-property-test-123456789", "code", "trace"
        )
    assert captured.value.detail == "search_console_property_not_authorized"


# --- Google Analytics -------------------------------------------------------
#
# The GA4 flow reuses every security-critical step of the Search Console one --
# the unscoped state read, the tenant adoption, the locking re-read, the
# single-use consume. What differs is the only thing that binds the credential
# to a site, so that is what these pin.


def analytics_records(
    *, property_ref: str = "properties/123456789", host: str = "wordkitapp.com"
) -> tuple[ConnectorOauthState, Connector, Site]:
    oauth_state, connector, site = callback_records()
    oauth_state.requested_scopes = [ANALYTICS_READONLY_SCOPE]
    oauth_state.requested_property_ref = property_ref
    connector.type = "google_analytics"
    site.normalized_host = host
    site.canonical_origin = f"https://{host}"
    return oauth_state, connector, site


class FakeAnalytics:
    def __init__(self, *properties: AnalyticsProperty) -> None:
        self.list_properties = AsyncMock(return_value=list(properties))


class AnalyticsSecretStore(FakeSecretStore):
    async def store(self, tenant_id, connector_id, provider, payload) -> str:
        assert provider == "google_analytics"
        self.payload = payload
        return "managed-secret://analytics"


async def complete_analytics(session, provider, analytics, secrets=None):
    return await ConnectorOAuthCallbackService(
        session, provider, secrets or AnalyticsSecretStore(), analytics
    ).complete_authorization("state-value-not-persisted-1234567890", "authorization-code", "trace")


def analytics_session(oauth_state, connector, site) -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[oauth_state, oauth_state, connector, site])
    session.execute = AsyncMock()
    session.add_all = MagicMock()
    return session


@pytest.mark.asyncio
async def test_a_property_whose_stream_is_the_site_is_connected() -> None:
    oauth_state, connector, site = analytics_records()
    session = analytics_session(oauth_state, connector, site)
    provider = FakeProvider(scopes=frozenset({ANALYTICS_READONLY_SCOPE}))
    analytics = FakeAnalytics(
        AnalyticsProperty("properties/123456789", "WordKit", ("wordkitapp.com",))
    )

    result = await complete_analytics(session, provider, analytics)

    assert result is connector
    assert connector.status == "active"
    assert connector.external_account_ref == "properties/123456789"
    assert connector.granted_scopes == [ANALYTICS_READONLY_SCOPE]
    # Search Console must not have been consulted for a GA4 state.
    provider.list_properties.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_property_measuring_someone_elses_site_is_refused() -> None:
    """The whole reason this connector reads data streams.

    `properties/123456789` names nothing, so without this an authorized account
    could attach any property in it to any site they had verified.
    """
    oauth_state, connector, site = analytics_records()
    session = analytics_session(oauth_state, connector, site)
    analytics = FakeAnalytics(
        AnalyticsProperty("properties/123456789", "Somebody Else", ("example.com",))
    )

    with pytest.raises(HTTPException) as captured:
        await complete_analytics(
            session, FakeProvider(scopes=frozenset({ANALYTICS_READONLY_SCOPE})), analytics
        )

    assert captured.value.detail == "analytics_property_site_mismatch"
    assert connector.status == "pending_authorization"
    assert connector.external_account_ref is None


@pytest.mark.asyncio
async def test_a_property_outside_the_grant_is_refused() -> None:
    oauth_state, connector, site = analytics_records()
    session = analytics_session(oauth_state, connector, site)
    analytics = FakeAnalytics(
        AnalyticsProperty("properties/999", "Another", ("wordkitapp.com",))
    )

    with pytest.raises(HTTPException) as captured:
        await complete_analytics(
            session, FakeProvider(scopes=frozenset({ANALYTICS_READONLY_SCOPE})), analytics
        )

    assert captured.value.detail == "analytics_property_not_authorized"
    assert connector.status == "pending_authorization"


@pytest.mark.asyncio
async def test_a_grant_carrying_the_wrong_scope_is_refused() -> None:
    """Google grants what the user consented to, not what was requested.

    A GA4 state redeemed against a Search Console grant would otherwise store a
    credential that cannot read the property it was connected for.
    """
    oauth_state, connector, site = analytics_records()
    session = analytics_session(oauth_state, connector, site)

    with pytest.raises(HTTPException) as captured:
        await complete_analytics(
            session,
            FakeProvider(scopes=frozenset({GSC_READONLY_SCOPE})),
            FakeAnalytics(AnalyticsProperty("properties/123456789", "W", ("wordkitapp.com",))),
        )

    assert captured.value.detail == "oauth_scope_mismatch"


@pytest.mark.asyncio
async def test_a_search_console_state_still_takes_the_search_console_path() -> None:
    """The dispatch must not have changed the flow that already worked."""
    oauth_state, connector, site = callback_records()
    session = analytics_session(oauth_state, connector, site)
    provider = FakeProvider()
    analytics = FakeAnalytics()

    result = await ConnectorOAuthCallbackService(
        session, provider, FakeSecretStore(), analytics
    ).complete_authorization("state-value-not-persisted-1234567890", "authorization-code", "trace")

    assert result.external_account_ref == "sc-domain:example.com"
    provider.list_properties.assert_awaited_once()
    analytics.list_properties.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_state_asking_for_an_unsupported_scope_is_refused() -> None:
    """A scope this build cannot bind must not reach a provider at all."""
    oauth_state, connector, site = analytics_records()
    oauth_state.requested_scopes = ["https://www.googleapis.com/auth/drive"]
    session = analytics_session(oauth_state, connector, site)
    provider = FakeProvider()

    with pytest.raises(HTTPException) as captured:
        await complete_analytics(session, provider, FakeAnalytics())

    assert captured.value.detail == "oauth_state_invalid"
    provider.exchange_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_ga4_callback_without_an_analytics_client_fails_closed() -> None:
    oauth_state, connector, site = analytics_records()
    session = analytics_session(oauth_state, connector, site)

    with pytest.raises(HTTPException) as captured:
        await ConnectorOAuthCallbackService(
            session, FakeProvider(scopes=frozenset({ANALYTICS_READONLY_SCOPE})), FakeSecretStore()
        ).complete_authorization("state-value-not-persisted-1234567890", "authorization-code", "trace")

    assert captured.value.status_code == 503
    assert captured.value.detail == "google_analytics_connector_not_configured"


# -- the route's own behaviour, which is not the service's ---------------------


@pytest.mark.asyncio
async def test_a_refusal_is_handed_to_the_page_that_started_the_flow() -> None:
    """A person is at the end of this redirect, not a client library.

    On 2026-09-08 a real connection attempt ended on
    `{"detail":"search_console_property_not_authorized"}` rendered as a bare
    JSON document at an /api/v1 URL. The refusal was correct, specific and
    actionable, and arrived in a form that offered no way to act on it and no
    way back. The settings page already knows what each of these codes means.
    """
    from app.api.routes import connectors as route

    settings = MagicMock()
    settings.google_connectors_enabled = True
    settings.connector_secret_backend = "database_envelope"
    settings.connector_secret_encryption_key = MagicMock(
        get_secret_value=MagicMock(return_value="0" * 44)
    )
    settings.connector_secret_key_version = "v1"
    settings.google_client_id = "client"
    settings.google_client_secret = MagicMock(get_secret_value=MagicMock(return_value="secret"))
    settings.google_oauth_redirect_uri = "https://app.example.com/api/v1/connectors/oauth/callback"
    settings.app_base_url = "https://app.example.com"

    refusal = HTTPException(status_code=409, detail="search_console_property_not_authorized")
    with (
        patch.object(route, "get_settings", return_value=settings),
        patch.object(route, "decode_encryption_key", return_value=b"k" * 32),
        patch.object(route, "DatabaseEnvelopeSecretStore", MagicMock()),
        patch.object(
            route.ConnectorOAuthCallbackService,
            "complete_authorization",
            new=AsyncMock(side_effect=refusal),
        ),
    ):
        response = await route.google_oauth_callback(AsyncMock(), state="s" * 32, code="c")

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://app.example.com/settings/connectors"
        "?error=search_console_property_not_authorized"
    )


@pytest.mark.asyncio
async def test_an_unexpected_failure_is_not_dressed_up_as_a_tidy_message() -> None:
    """Only a refusal is redirected.

    Turning a 500 into a sentence on a settings page is how a broken deployment
    comes to look merely unlucky. The refusals this redirects are decisions the
    service made on purpose; anything else still raises.
    """
    from app.api.routes import connectors as route

    settings = MagicMock()
    settings.google_connectors_enabled = True
    settings.connector_secret_backend = "database_envelope"
    settings.connector_secret_encryption_key = MagicMock(
        get_secret_value=MagicMock(return_value="0" * 44)
    )
    settings.connector_secret_key_version = "v1"
    settings.google_client_id = "client"
    settings.google_client_secret = MagicMock(get_secret_value=MagicMock(return_value="secret"))
    settings.google_oauth_redirect_uri = "https://app.example.com/api/v1/connectors/oauth/callback"
    settings.app_base_url = "https://app.example.com"

    with (
        patch.object(route, "get_settings", return_value=settings),
        patch.object(route, "decode_encryption_key", return_value=b"k" * 32),
        patch.object(route, "DatabaseEnvelopeSecretStore", MagicMock()),
        patch.object(
            route.ConnectorOAuthCallbackService,
            "complete_authorization",
            new=AsyncMock(side_effect=RuntimeError("the provider fell over")),
        ),
        pytest.raises(RuntimeError, match="the provider fell over"),
    ):
        await route.google_oauth_callback(AsyncMock(), state="s" * 32, code="c")
