import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import ConnectorOauthState, Site
from app.services.connectors import GSC_READONLY_SCOPE, ConnectorService, property_matches_site

TENANT_ID = UUID("019d0000-0000-7000-8000-000000000011")
ACTOR_ID = UUID("019d0000-0000-7000-8000-000000000031")
SITE_ID = UUID("019d0000-0000-7000-8000-000000000012")


@pytest.mark.asyncio
async def test_authorization_is_read_only_short_lived_and_state_is_only_hashed() -> None:
    site = Site(
        id=SITE_ID,
        tenant_id=TENANT_ID,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
        status="active",
        verified_at=datetime.now(UTC),
    )
    session = MagicMock()
    # Three reads, in order: the verified site, this tenant's own OAuth client
    # (none, so the deployment's is used -- which is what the `client_id`
    # assertion below now pins), and the existing connector.
    session.scalar = AsyncMock(side_effect=[site, None, None])
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    context = TenantContext(TENANT_ID, ACTOR_ID, Role.ADMIN, "trace")
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        cursor_signing_key="c" * 32,
        google_connectors_enabled=True,
        google_client_id="google-client",
        google_client_secret=SecretStr("not-returned"),
        search_query_hash_key=SecretStr("q" * 32),
        connector_secret_backend="database_envelope",
        connector_secret_encryption_key=SecretStr("eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="),
    )

    connector, authorization_url, expires_at = await ConnectorService(
        session, context
    ).begin_gsc_authorization(SITE_ID, "sc-domain:example.com", settings)

    query = parse_qs(urlsplit(authorization_url).query)
    state = query["state"][0]
    assert query["scope"] == [GSC_READONLY_SCOPE]
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["client_id"] == ["google-client"]
    assert "not-returned" not in authorization_url
    assert connector.secret_ref is None
    assert 0 < (expires_at - datetime.now(UTC)).total_seconds() <= 600

    oauth_states = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], ConnectorOauthState)
    ]
    assert len(oauth_states) == 1
    assert oauth_states[0].state_hash == hashlib.sha256(state.encode()).hexdigest()
    assert state not in repr(oauth_states[0].__dict__)


@pytest.mark.parametrize(
    ("property_ref", "host", "expected"),
    [
        ("sc-domain:example.com", "example.com", True),
        ("sc-domain:example.com", "www.example.com", True),
        ("https://www.example.com/", "www.example.com", True),
        ("https://attacker.example/", "example.com", False),
        ("sc-domain:attacker.example", "example.com", False),
    ],
)
def test_search_console_property_is_bound_to_verified_host(
    property_ref: str, host: str, expected: bool
) -> None:
    assert property_matches_site(property_ref, host) is expected
