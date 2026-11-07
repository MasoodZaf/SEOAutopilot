import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db.models import TenantBilling
from app.services.billing import has_access, parse_event, signature_is_valid

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
