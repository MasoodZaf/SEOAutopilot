"""Changing a site's operating mode, against PostgreSQL.

Mode was settable only when a site was created, so the product decision that
"operating mode is the tenant's choice" had no mechanism behind it: a customer
who had watched a site in Observe and wanted proposals had to delete the site
and make it again.

Mode is not a preference. It decides whether the platform may propose changes to
someone's website, and in Autopilot whether it may make them unattended. These
cases hold the transition to that: who may ask, what a site must already be, and
that the way back down is never blocked.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.schemas import SiteModeUpdate
from app.core.context import Role, TenantContext
from app.services.governance import GovernanceService
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]


async def _seed(session, ids: dict[str, UUID], *, mode: str, verified: bool) -> None:
    await session.execute(
        text("INSERT INTO tenant(id,slug,name,status) VALUES(:id,:slug,'mode','active')"),
        {"id": ids["tenant_id"], "slug": f"mode-{ids['tenant_id'].hex[:8]}"},
    )
    await session.execute(
        text(
            "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,mode,status,"
            "verified_at) VALUES(:id,:tenant_id,'m','https://mode.example','mode.example',"
            ":mode,:status,:verified)"
        ),
        {
            "id": ids["site_id"],
            "tenant_id": ids["tenant_id"],
            "mode": mode,
            "status": "active" if verified else "pending_verification",
            "verified": datetime(2026, 1, 1, tzinfo=UTC) if verified else None,
        },
    )


async def _teardown(session, tenant_id: UUID) -> None:
    for table in ("outbox_event", "audit_event", "site"):
        await session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
        )
    await session.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})


def _fixture(mode: str, verified: bool = True):
    @pytest_asyncio.fixture
    async def _make(engine):
        ids = {"tenant_id": uuid4(), "site_id": uuid4(), "actor_id": uuid4()}
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session, session.begin():
            await _seed(session, ids, mode=mode, verified=verified)
        yield ids
        async with factory() as session, session.begin():
            await _teardown(session, ids["tenant_id"])

    return _make


observing = _fixture("observe")
recommending = _fixture("recommend")
unverified = _fixture("observe", verified=False)


def scoped(app_engine, tenant_id: UUID):
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    class _Scoped:
        async def __aenter__(self):
            self._session = factory()
            await self._session.__aenter__()
            self._transaction = self._session.begin()
            await self._transaction.__aenter__()
            await self._session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            return self._session

        async def __aexit__(self, *exc):
            await self._session.rollback()
            await self._session.__aexit__(None, None, None)

    return _Scoped()


def governance(session, ids: dict[str, UUID], role: Role = Role.OWNER) -> GovernanceService:
    return GovernanceService(
        session,
        TenantContext(
            tenant_id=ids["tenant_id"],
            actor_id=ids["actor_id"],
            role=role,
            trace_id="integration",
        ),
    )


async def test_a_verified_site_moves_from_observe_to_recommend(app_engine, observing) -> None:
    async with scoped(app_engine, observing["tenant_id"]) as session:
        status_read = await governance(session, observing).set_site_mode(
            observing["site_id"], SiteModeUpdate(mode="recommend", reason="pilot is ready")
        )
        assert status_read.mode == "recommend"

        recorded = (
            await session.execute(
                text(
                    "SELECT action, metadata->>'previous_mode' AS previous,"
                    " metadata->>'reason' AS reason FROM audit_event"
                    " WHERE tenant_id=:t AND action='governance.site_mode_changed'"
                ),
                {"t": observing["tenant_id"]},
            )
        ).all()
        assert [(r.action, r.previous, r.reason) for r in recorded] == [
            ("governance.site_mode_changed", "observe", "pilot is ready")
        ]
        published = (
            await session.execute(
                text("SELECT event_type FROM outbox_event WHERE tenant_id=:t"),
                {"t": observing["tenant_id"]},
            )
        ).scalars().all()
        assert published == ["site.mode_changed.v1"]


async def test_an_unverified_site_cannot_be_given_more_authority(app_engine, unverified) -> None:
    """Ownership proof is the whole basis for touching someone's site."""
    async with scoped(app_engine, unverified["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await governance(session, unverified).set_site_mode(
                unverified["site_id"], SiteModeUpdate(mode="recommend", reason="impatient")
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "site_must_be_verified_before_raising_mode"


async def test_autopilot_mode_needs_autopilot_authority_granted_first(
    app_engine, recommending
) -> None:
    """Two separate acts, so one request cannot arrive at unattended changes."""
    async with scoped(app_engine, recommending["tenant_id"]) as session:
        with pytest.raises(HTTPException) as raised:
            await governance(session, recommending).set_site_mode(
                recommending["site_id"], SiteModeUpdate(mode="autopilot", reason="ship it")
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "autopilot_must_be_enabled_before_entering_autopilot_mode"


async def test_dropping_to_observe_is_never_blocked(app_engine, recommending) -> None:
    """The way to stop a site being changed must not itself be refusable."""
    async with scoped(app_engine, recommending["tenant_id"]) as session:
        await session.execute(
            text("UPDATE site SET emergency_freeze=true, status='pending_verification'"
                 " WHERE id=:id"),
            {"id": recommending["site_id"]},
        )
        status_read = await governance(session, recommending).set_site_mode(
            recommending["site_id"], SiteModeUpdate(mode="observe", reason="stand down")
        )

    assert status_read.mode == "observe"


async def test_leaving_autopilot_mode_withdraws_autopilot_authority(
    app_engine, recommending
) -> None:
    async with scoped(app_engine, recommending["tenant_id"]) as session:
        await session.execute(
            text("UPDATE site SET autopilot_enabled=true WHERE id=:id"),
            {"id": recommending["site_id"]},
        )
        status_read = await governance(session, recommending).set_site_mode(
            recommending["site_id"], SiteModeUpdate(mode="observe", reason="pausing")
        )

    assert status_read.mode == "observe"
    assert status_read.autopilot_enabled is False


async def test_a_frozen_site_cannot_be_raised(app_engine, observing) -> None:
    async with scoped(app_engine, observing["tenant_id"]) as session:
        await session.execute(
            text("UPDATE site SET emergency_freeze=true WHERE id=:id"),
            {"id": observing["site_id"]},
        )
        with pytest.raises(HTTPException) as raised:
            await governance(session, observing).set_site_mode(
                observing["site_id"], SiteModeUpdate(mode="recommend", reason="anyway")
            )

    assert raised.value.status_code == 409
    assert raised.value.detail == "site_is_frozen"


async def test_only_owners_and_admins_may_change_mode(app_engine, observing) -> None:
    for role in (Role.SEO_MANAGER, Role.EDITOR, Role.VIEWER):
        async with scoped(app_engine, observing["tenant_id"]) as session:
            with pytest.raises(HTTPException) as raised:
                await governance(session, observing, role).set_site_mode(
                    observing["site_id"], SiteModeUpdate(mode="recommend", reason="please")
                )
            assert raised.value.status_code == 403
            assert raised.value.detail == "only_admins_can_change_site_mode"


async def test_setting_the_mode_it_already_has_records_nothing(app_engine, observing) -> None:
    async with scoped(app_engine, observing["tenant_id"]) as session:
        status_read = await governance(session, observing).set_site_mode(
            observing["site_id"], SiteModeUpdate(mode="observe", reason="no change")
        )
        assert status_read.mode == "observe"
        events = (
            await session.execute(
                text("SELECT count(*) FROM audit_event WHERE tenant_id=:t"),
                {"t": observing["tenant_id"]},
            )
        ).scalar_one()

    assert events == 0


async def test_another_tenants_site_is_not_found(app_engine, observing) -> None:
    stranger = uuid4()
    async with scoped(app_engine, stranger) as session:
        service = GovernanceService(
            session,
            TenantContext(
                tenant_id=stranger, actor_id=uuid4(), role=Role.OWNER, trace_id="integration"
            ),
        )
        with pytest.raises(HTTPException) as raised:
            await service.set_site_mode(
                observing["site_id"], SiteModeUpdate(mode="recommend", reason="not mine")
            )

    assert raised.value.status_code == 404
    assert raised.value.detail == "site_not_found"


async def test_a_reason_is_required_and_the_mode_must_be_real() -> None:
    """A mode change is a decision someone has to own."""
    with pytest.raises(ValueError):
        SiteModeUpdate(mode="recommend", reason="")
    with pytest.raises(ValueError):
        SiteModeUpdate(mode="unattended", reason="a good reason")
