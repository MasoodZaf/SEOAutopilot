from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.db.models import AuditEvent, Connector, ConnectorOauthState, OutboxEvent, Site
from app.services.connectors import (
    GSC_READONLY_SCOPE,
    ConnectorOAuthCallbackService,
)
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

    async def store(self, tenant_id, connector_id, provider, payload):
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
    session.scalar = AsyncMock(side_effect=[oauth_state, connector, site])
    session.add_all = MagicMock()
    provider = FakeProvider()
    secrets = FakeSecretStore()

    result = await ConnectorOAuthCallbackService(session, provider, secrets).complete_gsc(
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
    provider = FakeProvider()

    with pytest.raises(HTTPException) as captured:
        await ConnectorOAuthCallbackService(session, provider, FakeSecretStore()).complete_gsc(
            "replayed-state-value-123456789012", "code", "trace"
        )
    assert captured.value.detail == "oauth_state_invalid"
    provider.exchange_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_rejects_broader_scope_without_storing_secret() -> None:
    oauth_state, connector, site = callback_records()
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[oauth_state, connector, site])
    provider = FakeProvider(
        scopes=frozenset(
            {GSC_READONLY_SCOPE, "https://www.googleapis.com/auth/webmasters"}
        )
    )
    secrets = FakeSecretStore()

    with pytest.raises(HTTPException) as captured:
        await ConnectorOAuthCallbackService(session, provider, secrets).complete_gsc(
            "state-value-for-scope-test-123456", "code", "trace"
        )
    assert captured.value.detail == "oauth_scope_mismatch"
    assert secrets.payload is None
    assert oauth_state.consumed_at is None


@pytest.mark.asyncio
async def test_callback_rejects_unverified_or_unrequested_property() -> None:
    oauth_state, connector, site = callback_records()
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[oauth_state, connector, site])
    provider = FakeProvider(
        properties=[GoogleProperty("sc-domain:example.com", "siteUnverifiedUser")]
    )

    with pytest.raises(HTTPException) as captured:
        await ConnectorOAuthCallbackService(session, provider, FakeSecretStore()).complete_gsc(
            "state-value-property-test-123456789", "code", "trace"
        )
    assert captured.value.detail == "search_console_property_not_authorized"
