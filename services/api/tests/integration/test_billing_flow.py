"""Billing against a real database, as the role that cannot bypass row-level security."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import TenantBilling
from app.services.billing import BillingService, apply_event, parse_event
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
SETTINGS = Settings(
    stripe_secret_key=SecretStr("sk_test_x"),
    stripe_webhook_secret=SecretStr("whsec_x"),
    stripe_price_id="price_x",
    app_base_url="https://app.example.com",
)


async def _tenant(engine, status: str = "trialing") -> UUID:
    tenant_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, slug, name, status) VALUES (:id, :slug, 'T', 'active')"),
            {"id": tenant_id, "slug": f"t-{tenant_id.hex}"},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_billing (tenant_id, status, trial_ends_at)"
                " VALUES (:id, :status, :trial)"
            ),
            {"id": tenant_id, "status": status, "trial": NOW + timedelta(days=14)},
        )
    return tenant_id


class _Scoped:
    def __init__(self, app_engine, tenant_id) -> None:
        self.factory = async_sessionmaker(app_engine, expire_on_commit=False)
        self.tenant_id = tenant_id

    async def __aenter__(self):
        self.session = self.factory()
        await self.session.begin()
        await self.session.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(self.tenant_id)}
        )
        return self.session

    async def __aexit__(self, kind, *_):
        await (self.session.rollback() if kind else self.session.commit())
        await self.session.close()


def _event(tenant_id, event_id="evt_1", status="active", created=NOW, customer="cus_1"):
    parsed = parse_event(
        {
            "id": f"{event_id}_{tenant_id.hex}",
            "type": "customer.subscription.updated",
            "created": int(created.timestamp()),
            "data": {
                "object": {
                    "id": "sub_" + tenant_id.hex,
                    "customer": customer + tenant_id.hex,
                    "status": status,
                    "metadata": {"tenant_id": str(tenant_id)},
                    "items": {"data": [{"current_period_end": int(NOW.timestamp()) + 86400}]},
                }
            },
        }
    )
    assert parsed is not None
    return parsed


async def _status(app_engine, tenant_id) -> str:
    async with _Scoped(app_engine, tenant_id) as session:
        row = await session.get(TenantBilling, tenant_id)
        assert row is not None
        return row.status


async def test_an_event_applies_once_and_an_older_one_cannot_undo_it(engine, app_engine) -> None:
    tenant_id = await _tenant(engine)
    async with _Scoped(app_engine, tenant_id) as session:
        assert await apply_event(session, _event(tenant_id), "t") == "applied"
    assert await _status(app_engine, tenant_id) == "active"

    async with _Scoped(app_engine, tenant_id) as session:
        assert await apply_event(session, _event(tenant_id), "t") == "duplicate"

    older = _event(tenant_id, "evt_0", "past_due", NOW - timedelta(hours=1))
    async with _Scoped(app_engine, tenant_id) as session:
        assert await apply_event(session, older, "t") == "stale"
    assert await _status(app_engine, tenant_id) == "active"


async def test_an_event_for_another_customer_changes_nothing(engine, app_engine) -> None:
    tenant_id = await _tenant(engine)
    async with _Scoped(app_engine, tenant_id) as session:
        await apply_event(session, _event(tenant_id), "t")
    other = _event(tenant_id, "evt_2", "canceled", NOW + timedelta(hours=1), customer="cus_other")
    async with _Scoped(app_engine, tenant_id) as session:
        assert await apply_event(session, other, "t") == "customer_mismatch"
    assert await _status(app_engine, tenant_id) == "active"


async def test_one_workspace_cannot_see_anothers_billing(engine, app_engine) -> None:
    mine, theirs = await _tenant(engine), await _tenant(engine)
    async with _Scoped(app_engine, mine) as session:
        assert await session.get(TenantBilling, theirs) is None


def _context(tenant_id, role=Role.OWNER) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, actor_id=uuid4(), role=role, trace_id="trace")


async def test_checkout_sends_the_server_side_tenant_and_keeps_the_trial(engine, app_engine) -> None:
    tenant_id = await _tenant(engine)
    requests: list[httpx.Request] = []

    def stripe(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/customers"):
            return httpx.Response(200, json={"id": "cus_" + tenant_id.hex})
        return httpx.Response(200, json={"url": "https://checkout.stripe.com/c/x"})

    async with _Scoped(app_engine, tenant_id) as session:
        url = await BillingService(
            session, _context(tenant_id), SETTINGS, httpx.MockTransport(stripe)
        ).start_checkout()
    assert url == "https://checkout.stripe.com/c/x"
    body = requests[-1].content.decode()
    assert str(tenant_id) in body and "price_x" in body
    assert "subscription_data%5Btrial_end%5D" in body
    assert "https%3A%2F%2Fapp.example.com%2Fsettings%2Fbilling" in body


async def test_only_an_owner_can_start_checkout(engine, app_engine) -> None:
    tenant_id = await _tenant(engine)
    async with _Scoped(app_engine, tenant_id) as session:
        with pytest.raises(HTTPException) as refused:
            await BillingService(
                session, _context(tenant_id, Role.ADMIN), SETTINGS
            ).start_checkout()
    assert refused.value.status_code == 403


async def test_a_comped_workspace_is_not_asked_to_pay(engine, app_engine) -> None:
    tenant_id = await _tenant(engine, "comped")
    async with _Scoped(app_engine, tenant_id) as session:
        with pytest.raises(HTTPException) as refused:
            await BillingService(session, _context(tenant_id), SETTINGS).start_checkout()
    assert refused.value.detail == "billing_not_required"


async def test_the_schema_allows_file_and_meta_tag_proof(engine) -> None:
    """The check constraint used to allow dns_txt only, which mocks cannot see."""
    async with engine.connect() as connection:
        definition = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'site_verification_challenge_method_check'"
            )
        )
    assert "file" in definition and "meta_tag" in definition


async def test_the_scheduler_query_reads_billing_as_postgres_understands_it(engine) -> None:
    """The worker has no database in its own tests, so its SQL is parsed here."""
    source = (
        Path(__file__).resolve().parents[3] / "worker" / "app" / "routines" / "scheduler.py"
    ).read_text()
    query = source.split('DUE_ROUTINES_SQL = """')[1].split('"""')[0]
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        await raw.driver_connection.prepare(query)
