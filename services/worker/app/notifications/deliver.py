"""Delivers report digests to configured outbound channels.

Outbound delivery is a write to a third party, so it is treated like any other
egress: https only, public destinations only, resolved-address checked at send
time, a bounded payload, and no evidence content beyond headline counts.
"""

import hashlib
import json
import logging
from typing import Any, Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.egress.guard import EgressBlocked
from app.egress.guard import assert_public_https_target as guard_public_https_target

logger = logging.getLogger(__name__)

CLAIM_BATCH = 10
MAX_ATTEMPTS = 5
LEASE_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 64 * 1024
IDLE_SLEEP_SECONDS = 20.0


class DeliveryError(Exception):
    """Carries a short, non-sensitive failure code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HttpClient(Protocol):
    async def post(self, url: str, *, json: Any, timeout: float) -> Any: ...


class Pool(Protocol):
    def acquire(self) -> Any: ...


def channel_aad(tenant_id: UUID, channel_kind: str, key_version: str) -> bytes:
    """Must match app.services.notifications.channel_aad in the API service."""
    return f"{tenant_id}:notification:{channel_kind}:{key_version}".encode()


def decrypt_webhook_url(
    key: bytes, tenant_id: UUID, kind: str, key_version: str,
    nonce: bytes, ciphertext: bytes, aad_hash: str,
) -> str:
    aad = channel_aad(tenant_id, kind, key_version)
    if hashlib.sha256(aad).hexdigest() != aad_hash:
        raise DeliveryError("channel_aad_mismatch")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad).decode()
    except Exception as error:
        raise DeliveryError("channel_secret_undecryptable") from error


def assert_public_https_target(url: str) -> None:
    """Delegates to the shared egress policy, re-raising as a delivery error."""
    try:
        guard_public_https_target(url)
    except EgressBlocked as error:
        raise DeliveryError(error.code) from error


def build_message(kind: str, site_name: str, payload: dict[str, Any], report_url: str) -> dict[str, Any]:
    """Headline counts only. Findings and URLs stay behind authentication."""
    opportunities = payload.get("opportunities", {})
    period = payload.get("period", {})
    search = payload.get("search", {})
    top_count = len(opportunities.get("top", []) or [])
    lines = [
        f"*{site_name}* — SEO Autopilot weekly digest",
        f"Period {period.get('start')} → {period.get('end')}",
        (
            f"{opportunities.get('opened_in_period', 0)} opportunities opened, "
            f"{opportunities.get('resolved_in_period', 0)} resolved, "
            f"{top_count} in the review queue."
        ),
    ]
    if search.get("available"):
        clicks = search.get("clicks", {})
        lines.append(
            f"Search clicks {clicks.get('current')} vs {clicks.get('previous')} "
            f"in the prior window (association, not attribution)."
        )
    else:
        lines.append("No Search Console evidence in this window.")
    lines.append(f"Full report: {report_url}")
    text = "\n".join(lines)
    if kind == "slack_webhook":
        return {"text": text}
    return {"kind": "weekly_digest", "site": site_name, "text": text, "report_url": report_url}


CLAIM_SQL = """
UPDATE notification_delivery nd
SET status='queued', attempts=nd.attempts+1, lease_until=now()+($1 * interval '1 second')
FROM notification_channel nc, report r, site s
WHERE nd.id IN (
    SELECT id FROM notification_delivery
    WHERE status='queued' AND attempts<$2 AND (lease_until IS NULL OR lease_until<now())
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT $3
  )
  AND nc.id=nd.channel_id AND nc.tenant_id=nd.tenant_id
  AND r.id=nd.report_id AND r.tenant_id=nd.tenant_id
  AND s.id=r.site_id AND s.tenant_id=r.tenant_id
RETURNING nd.id, nd.tenant_id, nd.report_id, nd.attempts,
          nc.kind, nc.nonce, nc.ciphertext, nc.aad_hash, nc.key_version,
          nc.enabled, nc.revoked_at,
          r.payload_json, s.name AS site_name
"""


async def deliver_batch(
    connection: Any, client: HttpClient, key: bytes, app_base_url: str
) -> int:
    rows = await connection.fetch(CLAIM_SQL, LEASE_SECONDS, MAX_ATTEMPTS, CLAIM_BATCH)
    delivered = 0
    for row in rows:
        delivery_id = row["id"]
        tenant_id = row["tenant_id"]
        if not row["enabled"] or row["revoked_at"] is not None:
            await connection.execute(
                "UPDATE notification_delivery SET status='blocked',lease_until=NULL,"
                "error_code='channel_revoked' WHERE id=$1 AND tenant_id=$2",
                delivery_id, tenant_id,
            )
            continue
        try:
            url = decrypt_webhook_url(
                key, tenant_id, row["kind"], row["key_version"],
                bytes(row["nonce"]), bytes(row["ciphertext"]), row["aad_hash"],
            )
            assert_public_https_target(url)
            payload = row["payload_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            message = build_message(
                row["kind"], row["site_name"], payload,
                f"{app_base_url.rstrip('/')}/pilot/reports/{row['report_id']}",
            )
            response = await client.post(url, json=message, timeout=REQUEST_TIMEOUT_SECONDS)
            status_code = getattr(response, "status_code", 0)
            if status_code >= 400:
                raise DeliveryError(f"destination_status_{status_code}"[:80])
        except DeliveryError as error:
            terminal = row["attempts"] >= MAX_ATTEMPTS
            await connection.execute(
                "UPDATE notification_delivery SET status=$3,lease_until=NULL,error_code=$4 "
                "WHERE id=$1 AND tenant_id=$2",
                delivery_id, tenant_id,
                "failed" if terminal else "queued",
                error.code,
            )
            continue
        except Exception:
            # Never log the destination: it is the secret.
            logger.exception("notification delivery failed", extra={"channel_kind": row["kind"]})
            await connection.execute(
                "UPDATE notification_delivery SET status=$3,lease_until=NULL,"
                "error_code='delivery_transport_error' WHERE id=$1 AND tenant_id=$2",
                delivery_id, tenant_id,
                "failed" if row["attempts"] >= MAX_ATTEMPTS else "queued",
            )
            continue
        await connection.execute(
            "UPDATE notification_delivery SET status='delivered',delivered_at=now(),"
            "lease_until=NULL,error_code=NULL WHERE id=$1 AND tenant_id=$2",
            delivery_id, tenant_id,
        )
        delivered += 1
    return delivered


async def run_notification_dispatcher(
    pool: Pool, client: HttpClient, key: bytes, app_base_url: str, sleep: Any = None
) -> None:
    import asyncio

    pause = sleep or asyncio.sleep
    while True:
        try:
            async with pool.acquire() as connection:
                delivered = await deliver_batch(connection, client, key, app_base_url)
            await pause(1.0 if delivered else IDLE_SLEEP_SECONDS)
        except Exception:
            logger.exception("notification dispatcher tick failed")
            await pause(IDLE_SLEEP_SECONDS)
