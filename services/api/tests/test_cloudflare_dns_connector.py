from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.core.context import Role, TenantContext
from app.db.models import Connector, Site, SiteVerificationChallenge
from app.services.connectors import DNS_PROVIDER_CONNECTOR, ConnectorService
from app.services.dns_provider import DnsTxtRecord, DnsZone

TENANT_ID = UUID("019d0000-0000-7000-8000-000000000011")
ACTOR_ID = UUID("019d0000-0000-7000-8000-000000000031")
SITE_ID = UUID("019d0000-0000-7000-8000-000000000012")
ZONE_ID = "a" * 32


class FakeSecrets:
    def __init__(self) -> None:
        self.stored: dict[str, object] | None = None

    async def store(
        self, tenant_id: UUID, connector_id: UUID, provider: str, payload: dict[str, object]
    ) -> str:
        self.stored = payload
        return "managed-secret://opaque"

    async def load(self, tenant_id: UUID, connector_id: UUID, secret_ref: str) -> dict[str, object]:
        return {"api_token": "cloudflare-token-not-to-log"}


class FakeCloudflare:
    provider_key = "cloudflare"

    async def get_zone(self, zone_id, token):
        assert token == "cloudflare-token-not-to-log"
        return DnsZone(id=zone_id, name="example.com")

    async def find_txt_record(self, zone_id, name, content, token):
        return None

    async def create_txt_record(self, zone_id, name, content, token):
        return DnsTxtRecord(id="record-opaque", name=name, content=content)


def active_site() -> Site:
    return Site(
        id=SITE_ID,
        tenant_id=TENANT_ID,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
        status="pending_verification",
    )


@pytest.mark.asyncio
async def test_cloudflare_connection_binds_exact_zone_and_keeps_token_out_of_audit() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[active_site(), None])
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    secrets = FakeSecrets()
    service = ConnectorService(session, TenantContext(TENANT_ID, ACTOR_ID, Role.OWNER, "trace"))

    connector = await service.connect_dns_provider(
        SITE_ID,
        "cloudflare",
        ZONE_ID,
        "cloudflare-token-not-to-log",
        FakeCloudflare(),
        secrets,
    )

    assert connector.type == DNS_PROVIDER_CONNECTOR
    assert connector.provider_key == "cloudflare"
    assert connector.status == "active"
    assert connector.external_account_ref == ZONE_ID
    assert secrets.stored == {"api_token": "cloudflare-token-not-to-log"}
    audit_calls = [call.args[0] for call in session.add_all.call_args_list]
    assert all("cloudflare-token-not-to-log" not in repr(call) for call in audit_calls)


@pytest.mark.asyncio
async def test_cloudflare_dns_publish_requires_current_challenge_and_is_server_derived() -> None:
    site = active_site()
    challenge = SiteVerificationChallenge(
        id=UUID("019d0000-0000-7000-8000-000000000099"),
        tenant_id=TENANT_ID,
        site_id=SITE_ID,
        method="dns_txt",
        token_hash="x" * 64,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        created_by=ACTOR_ID,
    )
    connector = Connector(
        id=UUID("019d0000-0000-7000-8000-000000000098"),
        tenant_id=TENANT_ID,
        site_id=SITE_ID,
        type=DNS_PROVIDER_CONNECTOR,
        provider_key="cloudflare",
        status="active",
        external_account_ref=ZONE_ID,
        secret_ref="managed-secret://opaque",
    )
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[site, challenge, connector])
    session.add_all = MagicMock()
    service = ConnectorService(session, TenantContext(TENANT_ID, ACTOR_ID, Role.ADMIN, "trace"))
    provider = FakeCloudflare()

    record_id, record_name = await service.publish_dns_provider_challenge(
        SITE_ID, "cloudflare", "challenge-token", provider, FakeSecrets()
    )

    assert (record_id, record_name) == ("record-opaque", "_seo-autopilot.example.com")
    staged = session.add_all.call_args.args[0]
    assert "challenge-token" not in repr(staged)


@pytest.mark.asyncio
async def test_cloudflare_connection_rejects_zone_for_another_site() -> None:
    class WrongZone(FakeCloudflare):
        async def get_zone(self, zone_id, token):
            return DnsZone(id=zone_id, name="attacker.example")

    session = MagicMock()
    session.scalar = AsyncMock(return_value=active_site())
    session.add = MagicMock()
    session.flush = AsyncMock()
    service = ConnectorService(session, TenantContext(TENANT_ID, ACTOR_ID, Role.OWNER, "trace"))
    with pytest.raises(HTTPException, match="dns_provider_zone_site_mismatch"):
        await service.connect_dns_provider(
            SITE_ID,
            "cloudflare",
            ZONE_ID,
            "cloudflare-token-not-to-log",
            WrongZone(),
            FakeSecrets(),
        )
