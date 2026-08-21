from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CrawlCreate, SiteCreate
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, CrawlJob, OutboxEvent, Site, SiteVerificationChallenge
from app.services.sites import SiteService


def context(role: Role = Role.OWNER) -> TenantContext:
    return TenantContext(tenant_id=uuid4(), actor_id=uuid4(), role=role, trace_id="trace-test")


def session_mock() -> tuple[AsyncSession, MagicMock]:
    mocked = MagicMock(spec=AsyncSession)

    async def flush() -> None:
        site = cast(Site, mocked.add.call_args.args[0])
        site.id = UUID("019d0000-0000-7000-8000-000000000001")

    mocked.flush = AsyncMock(side_effect=flush)
    return cast(AsyncSession, mocked), mocked


@pytest.mark.asyncio
async def test_create_site_stages_site_audit_and_outbox() -> None:
    tenant_context = context()
    session, mocked = session_mock()
    result = await SiteService(session, tenant_context).create_site(
        SiteCreate(name=" Example ", canonical_origin="https://example.com")
    )
    assert result.tenant_id == tenant_context.tenant_id
    assert result.name == "Example"
    staged = mocked.add_all.call_args.args[0]
    assert isinstance(staged[0], AuditEvent)
    assert isinstance(staged[1], OutboxEvent)
    assert staged[0].tenant_id == tenant_context.tenant_id
    assert staged[1].payload["site_id"] == str(result.id)


@pytest.mark.asyncio
async def test_viewer_cannot_create_site() -> None:
    session, mocked = session_mock()
    with pytest.raises(HTTPException) as error:
        await SiteService(session, context(Role.VIEWER)).create_site(
            SiteCreate(name="Example", canonical_origin="https://example.com")
        )
    assert error.value.status_code == 403
    mocked.add.assert_not_called()


@pytest.mark.asyncio
async def test_challenge_stores_hash_not_raw_token() -> None:
    tenant_context = context()
    session, mocked = session_mock()
    site = Site(
        id=UUID("019d0000-0000-7000-8000-000000000004"),
        tenant_id=tenant_context.tenant_id,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
    )
    mocked.scalar = AsyncMock(side_effect=[site, None])

    async def flush_challenge() -> None:
        challenge = cast(SiteVerificationChallenge, mocked.add.call_args.args[0])
        challenge.id = UUID("019d0000-0000-7000-8000-000000000005")

    mocked.flush = AsyncMock(side_effect=flush_challenge)
    challenge, token = await SiteService(session, tenant_context).create_verification_challenge(
        site.id
    )
    assert challenge.token_hash != token
    assert len(challenge.token_hash) == 64
    assert challenge.status == "pending"


@pytest.mark.asyncio
async def test_crawl_requires_verified_site() -> None:
    tenant_context = context()
    session, mocked = session_mock()
    site = Site(
        id=UUID("019d0000-0000-7000-8000-000000000006"),
        tenant_id=tenant_context.tenant_id,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
        status="pending_verification",
    )
    mocked.scalar = AsyncMock(return_value=site)
    with pytest.raises(HTTPException) as error:
        await SiteService(session, tenant_context).create_crawl(site.id, CrawlCreate())
    assert error.value.status_code == 409
    assert error.value.detail == "site_not_verified"


@pytest.mark.asyncio
async def test_verified_site_creates_crawl_and_outbox_event() -> None:
    tenant_context = context(Role.DEVELOPER)
    session, mocked = session_mock()
    site = Site(
        id=UUID("019d0000-0000-7000-8000-000000000007"),
        tenant_id=tenant_context.tenant_id,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
        status="active",
        verified_at=datetime.now(UTC),
    )
    mocked.scalar = AsyncMock(side_effect=[site, None])

    async def flush_crawl() -> None:
        crawl = cast(CrawlJob, mocked.add.call_args.args[0])
        crawl.id = UUID("019d0000-0000-7000-8000-000000000008")

    mocked.flush = AsyncMock(side_effect=flush_crawl)
    crawl = await SiteService(session, tenant_context).create_crawl(
        site.id, CrawlCreate(max_pages=250, render_policy="auto")
    )
    assert crawl.status == "queued"
    assert crawl.config_snapshot["max_pages"] == 250
    assert crawl.config_snapshot["max_depth"] == 10
    events = mocked.add_all.call_args.args[0]
    assert events[1].event_type == "crawl.requested"


@pytest.mark.asyncio
async def test_active_crawl_blocks_duplicate_request() -> None:
    tenant_context = context(Role.DEVELOPER)
    site = Site(
        id=UUID("019d0000-0000-7000-8000-000000000009"),
        tenant_id=tenant_context.tenant_id,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
        status="active",
        verified_at=datetime.now(UTC),
    )
    active = CrawlJob(
        id=UUID("019d0000-0000-7000-8000-000000000010"),
        tenant_id=tenant_context.tenant_id,
        site_id=site.id,
        status="running",
        requested_by=tenant_context.actor_id,
        config_snapshot={},
    )
    session, mocked = session_mock()
    mocked.scalar = AsyncMock(side_effect=[site, active])

    with pytest.raises(HTTPException) as error:
        await SiteService(session, tenant_context).create_crawl(site.id, CrawlCreate())

    assert error.value.status_code == 409
    assert error.value.detail == "crawl_already_active"
    mocked.add.assert_not_called()


@pytest.mark.asyncio
async def test_latest_crawl_is_resolved_inside_tenant_site_scope() -> None:
    tenant_context = context(Role.VIEWER)
    site = Site(
        id=UUID("019d0000-0000-7000-8000-000000000011"),
        tenant_id=tenant_context.tenant_id,
        name="Example",
        canonical_origin="https://example.com",
        normalized_host="example.com",
    )
    crawl = CrawlJob(
        id=UUID("019d0000-0000-7000-8000-000000000012"),
        tenant_id=tenant_context.tenant_id,
        site_id=site.id,
        status="completed",
        requested_by=tenant_context.actor_id,
        config_snapshot={},
    )
    session, mocked = session_mock()
    mocked.scalar = AsyncMock(side_effect=[site, crawl])

    result = await SiteService(session, tenant_context).latest_crawl(site.id)

    assert result is crawl
    assert mocked.scalar.await_count == 2
