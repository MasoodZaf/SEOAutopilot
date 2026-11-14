import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.db.models import TenantBilling
from app.services.billing import as_current_mode, has_access, parse_event, signature_is_valid

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
SECRET = "whsec_test"


def _signed(payload: bytes, at: datetime = NOW, secret: str = SECRET) -> str:
    stamp = str(int(at.timestamp()))
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def test_a_correct_signature_is_accepted() -> None:
    assert signature_is_valid(b"{}", _signed(b"{}"), SECRET, NOW)


def test_a_tampered_body_or_another_secret_is_refused() -> None:
    assert not signature_is_valid(b'{"a":1}', _signed(b"{}"), SECRET, NOW)
    assert not signature_is_valid(b"{}", _signed(b"{}", secret="other"), SECRET, NOW)


def test_a_replayed_old_signature_is_refused() -> None:
    assert not signature_is_valid(b"{}", _signed(b"{}", NOW - timedelta(minutes=6)), SECRET, NOW)


def test_a_malformed_header_is_refused() -> None:
    for header in ("", "t=abc,v1=x", "v1=x", "t=1"):
        assert not signature_is_valid(b"{}", header, SECRET, NOW)


def _row(status: str, trial_ends_at: datetime | None = None) -> TenantBilling:
    return TenantBilling(tenant_id=uuid4(), status=status, trial_ends_at=trial_ends_at)


def test_who_has_access() -> None:
    assert has_access(_row("comped"), NOW)
    assert has_access(_row("active"), NOW)
    assert has_access(_row("past_due"), NOW)
    assert has_access(_row("trialing", NOW + timedelta(days=1)), NOW)
    assert not has_access(_row("trialing", NOW - timedelta(seconds=1)), NOW)
    for status in ("canceled", "unpaid", "incomplete", "incomplete_expired", "paused"):
        assert not has_access(_row(status), NOW)
    assert not has_access(None, NOW)


def test_stripe_state_from_the_other_mode_counts_only_for_the_trial() -> None:
    paid = _row("active")
    paid.stripe_customer_id, paid.stripe_livemode = "cus_test", False
    assert has_access(as_current_mode(paid, False), NOW)
    assert has_access(as_current_mode(paid, None), NOW)
    under_live = as_current_mode(paid, True)
    assert under_live is not None and under_live is not paid
    assert under_live.status == "canceled" and under_live.stripe_customer_id is None
    assert not has_access(under_live, NOW)
    assert paid.status == "active", "the stored row is not changed by looking at it"

    trial = _row("trialing", NOW + timedelta(days=2))
    trial.stripe_livemode = False
    assert has_access(as_current_mode(trial, True), NOW)

    unknown = _row("active")
    unknown.stripe_customer_id = "cus_old"
    assert as_current_mode(unknown, True) is unknown


def test_the_key_says_which_stripe_mode_is_in_use() -> None:
    assert Settings(stripe_secret_key=SecretStr("rk_live_x")).stripe_livemode is True
    assert Settings(stripe_secret_key=SecretStr("sk_live_x")).stripe_livemode is True
    assert Settings(stripe_secret_key=SecretStr("rk_test_x")).stripe_livemode is False
    assert Settings().stripe_livemode is None
    with pytest.raises(ValidationError):
        Settings(stripe_secret_key=SecretStr("pk_live_x"))


def test_an_event_says_which_mode_it_came_from() -> None:
    event = {
        "id": "evt_1",
        "type": "customer.subscription.updated",
        "created": int(NOW.timestamp()),
        "data": {"object": {"id": "sub_1", "metadata": {"tenant_id": str(uuid4())}}},
    }
    assert parse_event(event).livemode is None  # type: ignore[union-attr]
    assert parse_event({**event, "livemode": True}).livemode is True  # type: ignore[union-attr]
    assert parse_event({**event, "livemode": "yes"}).livemode is None  # type: ignore[union-attr]


def test_only_subscription_events_with_a_real_tenant_are_acted_on() -> None:
    tenant = uuid4()
    event = {
        "id": "evt_1",
        "type": "customer.subscription.updated",
        "created": int(NOW.timestamp()),
        "data": {"object": {"id": "sub_1", "customer": "cus_1", "metadata": {"tenant_id": str(tenant)}}},
    }
    parsed = parse_event(event)
    assert parsed is not None and parsed.tenant_id == tenant and parsed.subscription_id == "sub_1"
    assert parse_event({**event, "type": "invoice.paid"}) is None
    event["data"]["object"]["metadata"] = {"tenant_id": "not-a-uuid"}
    assert parse_event(event) is None
