"""A workspace's subscription: Stripe holds it, and this mirrors what Stripe says.

Whether a workspace may act is decided here from the mirrored row, so a Stripe
outage never locks anybody out. Reads are never refused: somebody whose trial
ended can still see and take away their own work. See ADR-010.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantContextDependency
from app.core.config import Settings, get_settings
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, BillingEvent, Site, TenantBilling
from app.db.session import TenantSession
from app.services.sites import stable_hash

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE = timedelta(minutes=5)
# Stripe refuses a trial_end closer than this, so a nearly finished trial just ends.
MIN_STRIPE_TRIAL = timedelta(hours=49)

STRIPE_STATUSES = {
    "trialing",
    "active",
    "past_due",
    "canceled",
    "unpaid",
    "incomplete",
    "incomplete_expired",
    "paused",
}


def has_access(row: TenantBilling | None, now: datetime | None = None) -> bool:
    """past_due still has access: Stripe is retrying the card and will say when it stops."""
    if row is None:
        return False
    if row.status in {"comped", "active", "past_due"}:
        return True
    if row.status == "trialing":
        return row.trial_ends_at is not None and row.trial_ends_at > (now or datetime.now(UTC))
    return False


def new_trial(tenant_id: UUID, settings: Settings) -> TenantBilling:
    return TenantBilling(
        tenant_id=tenant_id,
        status="trialing",
        trial_ends_at=datetime.now(UTC) + timedelta(days=settings.billing_trial_days),
    )


def signature_is_valid(payload: bytes, header: str, secret: str, now: datetime) -> bool:
    fields = [part.split("=", 1) for part in header.split(",") if "=" in part]
    timestamps = [value for key, value in fields if key == "t"]
    signatures = [value for key, value in fields if key == "v1"]
    if len(timestamps) != 1 or not signatures or not timestamps[0].isdigit():
        return False
    signed_at = datetime.fromtimestamp(int(timestamps[0]), UTC)
    if abs(now - signed_at) > SIGNATURE_TOLERANCE:
        return False
    expected = hmac.new(
        secret.encode(), timestamps[0].encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def _audit(
    tenant_id: UUID,
    action: str,
    actor_type: str,
    actor_id: str,
    trace_id: str,
    payload: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type="tenant_billing",
        resource_id=str(tenant_id),
        trace_id=trace_id,
        metadata_json=payload,
        event_hash=stable_hash({**payload, "event_type": action, "actor_id": actor_id}),
    )


class StripeClient:
    """Only the three calls this needs, and never a Stripe body in a log or an error."""

    def __init__(self, secret_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.secret_key = secret_key
        self.transport = transport

    async def post(self, path: str, form: dict[str, str], idempotency_key: str | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.secret_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=20.0) as client:
                response = await client.post(f"{STRIPE_API}{path}", data=form, headers=headers)
        except httpx.HTTPError as error:
            logger.warning("stripe unreachable", extra={"error": error.__class__.__name__})
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "billing_provider_error") from error
        if response.is_error:
            logger.warning("stripe refused", extra={"status_code": response.status_code})
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "billing_provider_error")
        return response.json()


def _require_enabled(settings: Settings) -> str:
    if not settings.billing_configured or settings.stripe_secret_key is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "billing_not_enabled")
    return settings.stripe_secret_key.get_secret_value()


class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.settings = settings
        self.transport = transport

    async def get(self) -> TenantBilling | None:
        return await self.session.scalar(
            select(TenantBilling).where(TenantBilling.tenant_id == self.context.tenant_id)
        )

    async def _customer(self, stripe: StripeClient, row: TenantBilling) -> str:
        if row.stripe_customer_id:
            return row.stripe_customer_id
        tenant_id = str(self.context.tenant_id)
        customer = await stripe.post(
            "/customers",
            {"metadata[tenant_id]": tenant_id},
            idempotency_key=f"customer-{tenant_id}",
        )
        customer_id = str(customer["id"])
        row.stripe_customer_id = customer_id
        await self.session.flush()
        return customer_id

    async def start_checkout(self) -> str:
        self.context.require(Role.OWNER)
        stripe = StripeClient(_require_enabled(self.settings), self.transport)
        row = await self.get()
        if row is None:
            row = new_trial(self.context.tenant_id, self.settings)
            self.session.add(row)
            await self.session.flush()
        if row.status == "comped":
            raise HTTPException(status.HTTP_409_CONFLICT, "billing_not_required")
        if row.status in {"active", "past_due"}:
            raise HTTPException(status.HTTP_409_CONFLICT, "subscription_already_active")
        customer = await self._customer(stripe, row)
        tenant_id = str(self.context.tenant_id)
        base = self.settings.app_base_url.rstrip("/")
        form = {
            "mode": "subscription",
            "customer": customer,
            "client_reference_id": tenant_id,
            "line_items[0][price]": str(self.settings.stripe_price_id),
            "line_items[0][quantity]": "1",
            "subscription_data[metadata][tenant_id]": tenant_id,
            "success_url": f"{base}/settings/billing?checkout=done",
            "cancel_url": f"{base}/settings/billing",
        }
        now = datetime.now(UTC)
        # Subscribing early does not forfeit the days of trial that are left.
        if row.trial_ends_at and row.trial_ends_at - now > MIN_STRIPE_TRIAL:
            form["subscription_data[trial_end]"] = str(int(row.trial_ends_at.timestamp()))
        checkout = await stripe.post("/checkout/sessions", form)
        self.session.add(
            _audit(
                self.context.tenant_id,
                "billing.checkout_started",
                "user",
                str(self.context.actor_id),
                self.context.trace_id,
                {"tenant_id": tenant_id},
            )
        )
        await self.session.flush()
        return str(checkout["url"])

    async def open_portal(self) -> str:
        self.context.require(Role.OWNER)
        stripe = StripeClient(_require_enabled(self.settings), self.transport)
        row = await self.get()
        if row is None or not row.stripe_customer_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "no_billing_account")
        portal = await stripe.post(
            "/billing_portal/sessions",
            {
                "customer": row.stripe_customer_id,
                "return_url": f"{self.settings.app_base_url.rstrip('/')}/settings/billing",
            },
        )
        return str(portal["url"])


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event_id: str
    event_type: str
    created: datetime
    tenant_id: UUID
    customer: str | None
    subscription: dict[str, Any] | None
    subscription_id: str | None


def parse_event(event: dict[str, Any]) -> ParsedEvent | None:
    """The fields we act on, or None for an event that is not about a workspace of ours.

    The tenant comes from metadata this service set on a signed event. It is
    still checked against the customer on file before anything is written.
    """
    event_type = str(event.get("type", ""))
    obj = (event.get("data") or {}).get("object") or {}
    if event_type == "checkout.session.completed":
        raw_tenant = obj.get("client_reference_id")
        subscription, subscription_id = None, obj.get("subscription")
    elif event_type.startswith("customer.subscription."):
        raw_tenant = (obj.get("metadata") or {}).get("tenant_id")
        subscription, subscription_id = obj, obj.get("id")
    else:
        return None
    try:
        tenant_id = UUID(str(raw_tenant))
        created = datetime.fromtimestamp(int(event["created"]), UTC)
        event_id = str(event["id"])
    except (KeyError, TypeError, ValueError):
        return None
    return ParsedEvent(
        event_id=event_id,
        event_type=event_type,
        created=created,
        tenant_id=tenant_id,
        customer=obj.get("customer"),
        subscription=subscription,
        subscription_id=subscription_id,
    )


def _timestamp(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def _period_end(subscription: dict[str, Any]) -> datetime | None:
    # Newer Stripe API versions moved this from the subscription to its items.
    if subscription.get("current_period_end"):
        return _timestamp(subscription["current_period_end"])
    items = (subscription.get("items") or {}).get("data") or []
    return _timestamp(items[0].get("current_period_end")) if items else None


async def apply_event(session: AsyncSession, event: ParsedEvent, trace_id: str) -> str:
    """Apply one verified event in a session scoped to its tenant. Returns the outcome."""
    row = await session.scalar(
        select(TenantBilling).where(TenantBilling.tenant_id == event.tenant_id).with_for_update()
    )
    if row is None:
        return "unknown_workspace"
    if row.stripe_customer_id and event.customer and row.stripe_customer_id != event.customer:
        logger.warning("billing event for another customer", extra={"event_id": event.event_id})
        return "customer_mismatch"
    inserted = await session.execute(
        insert(BillingEvent)
        .values(
            stripe_event_id=event.event_id,
            tenant_id=event.tenant_id,
            event_type=event.event_type,
        )
        .on_conflict_do_nothing()
        .returning(BillingEvent.stripe_event_id)
    )
    if inserted.first() is None:
        return "duplicate"
    if event.customer:
        row.stripe_customer_id = event.customer
    if event.subscription_id:
        row.stripe_subscription_id = event.subscription_id
    if event.subscription is not None:
        if row.last_event_at and event.created < row.last_event_at:
            return "stale"
        status_value = str(event.subscription.get("status"))
        if status_value not in STRIPE_STATUSES:
            logger.warning("unknown subscription status", extra={"event_id": event.event_id})
            return "unknown_status"
        row.status = status_value
        row.trial_ends_at = _timestamp(event.subscription.get("trial_end")) or (
            row.trial_ends_at if status_value == "trialing" else None
        )
        row.current_period_end = _period_end(event.subscription)
        row.cancel_at_period_end = bool(event.subscription.get("cancel_at_period_end"))
        row.last_event_at = event.created
        session.add(
            _audit(
                event.tenant_id,
                "billing.subscription_changed",
                "system",
                "stripe",
                trace_id,
                {"status": status_value, "stripe_event_id": event.event_id},
            )
        )
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return "applied"


async def _require_active_subscription(
    context: TenantContextDependency, session: TenantSession
) -> None:
    if not get_settings().billing_enforced:
        return
    row = await BillingService(session, context, get_settings()).get()
    if not has_access(row):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "subscription_required")


RequireSubscription = Depends(_require_active_subscription)


async def require_room_for_site(
    session: AsyncSession, context: TenantContext, settings: Settings
) -> None:
    """A paid plan covers a limited number of sites. Comped workspaces have no limit.

    The billing row is locked so two requests at once cannot both take the last place.
    """
    if not settings.billing_enforced:
        return
    row = await session.scalar(
        select(TenantBilling)
        .where(TenantBilling.tenant_id == context.tenant_id)
        .with_for_update()
    )
    if row is not None and row.status == "comped":
        return
    count = await session.scalar(
        select(func.count()).select_from(Site).where(Site.tenant_id == context.tenant_id)
    )
    if int(count or 0) >= settings.billing_site_limit:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "site_limit_reached")
