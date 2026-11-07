"""The workspace's subscription, and the signed webhook Stripe reports changes to."""

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.api.schemas import (
    BillingEnvelope,
    BillingRead,
    BillingRedirectEnvelope,
    BillingRedirectRead,
)
from app.core.auth import TenantContextDependency
from app.core.config import get_settings
from app.db.session import TenantSession, tenant_scoped_session
from app.services.billing import (
    BillingService,
    apply_event,
    has_access,
    parse_event,
    signature_is_valid,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.get("", response_model=BillingEnvelope)
async def get_billing(context: TenantContextDependency, session: TenantSession) -> BillingEnvelope:
    settings = get_settings()
    row = await BillingService(session, context, settings).get()
    return BillingEnvelope(
        data=BillingRead(
            configured=settings.billing_configured,
            enforced=settings.billing_enforced,
            status=row.status if row else "none",
            has_access=has_access(row),
            trial_ends_at=row.trial_ends_at if row else None,
            current_period_end=row.current_period_end if row else None,
            cancel_at_period_end=row.cancel_at_period_end if row else False,
            has_billing_account=bool(row and row.stripe_customer_id),
        ),
        meta={"trace_id": context.trace_id},
    )


@router.post("/checkout", response_model=BillingRedirectEnvelope)
async def start_checkout(
    context: TenantContextDependency, session: TenantSession
) -> BillingRedirectEnvelope:
    url = await BillingService(session, context, get_settings()).start_checkout()
    return BillingRedirectEnvelope(
        data=BillingRedirectRead(url=url), meta={"trace_id": context.trace_id}
    )


@router.post("/portal", response_model=BillingRedirectEnvelope)
async def open_portal(
    context: TenantContextDependency, session: TenantSession
) -> BillingRedirectEnvelope:
    url = await BillingService(session, context, get_settings()).open_portal()
    return BillingRedirectEnvelope(
        data=BillingRedirectRead(url=url), meta={"trace_id": context.trace_id}
    )


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request) -> dict[str, str]:
    """No session and no client identity: the signature is the only authority here."""
    settings = get_settings()
    if not settings.billing_configured or settings.stripe_webhook_secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "billing_not_enabled")
    payload = await request.body()
    secret = settings.stripe_webhook_secret.get_secret_value()
    header = request.headers.get("stripe-signature", "")
    if not signature_is_valid(payload, header, secret, datetime.now(UTC)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_signature")
    try:
        event = parse_event(json.loads(payload))
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_payload") from error
    if event is None:
        return {"outcome": "ignored"}
    try:
        async with tenant_scoped_session(event.tenant_id) as session:
            outcome = await apply_event(session, event, f"stripe-{uuid4().hex[:12]}")
    except IntegrityError:
        # The customer id already belongs to another workspace. Retrying cannot fix that.
        logger.warning("billing event conflicted", extra={"event_id": event.event_id})
        outcome = "conflict"
    return {"outcome": outcome}
