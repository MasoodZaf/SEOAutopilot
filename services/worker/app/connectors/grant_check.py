"""Prove each Google grant still works, daily, whether or not anything synced.

A grant used to be tested only by the sync that needed it. A site with no
scheduled sync was never tested at all, and one with a daily sync found out its
grant was dead only at 05:00 the next morning -- and then said so to nobody but
the `connector_sync` row. On 2026-09-18 all four Google connectors in production
had been dead for three to five days.

This renews each active Google grant that nothing has proven good for a day.
Renewing is the test: Google either issues an access token or says the grant is
gone. The two outcomes are written where the connection manager reads them --
`last_checked_at` on success, `reauthorization_required` and a reason on a
refusal -- so a dead grant is on the tenant's screen the day it dies.

What this cannot do is keep a grant alive. A refresh token that Google has
expired (7 days, for an app whose consent screen is still in Testing) cannot be
renewed by anything but a person consenting again.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.analytics.client import ANALYTICS_READONLY_SCOPE
from app.connectors.google_oauth import (
    GoogleAuthorizationRevoked,
    GoogleRefreshError,
    TokenRefresher,
)
from app.connectors.runtime import AccessTokenManager, set_tenant
from app.gsc.client import READONLY_SCOPE as GSC_READONLY_SCOPE

logger = logging.getLogger(__name__)

EXPECTED_SCOPES = {
    "google_search_console": frozenset({GSC_READONLY_SCOPE}),
    "google_analytics": frozenset({ANALYTICS_READONLY_SCOPE}),
}

# A grant proven good within this window is left alone. A sync renewing a token
# stamps `last_checked_at` too, so a site that syncs daily is never renewed
# twice for the same day.
CHECK_AFTER_HOURS = 20
SWEEP_INTERVAL_SECONDS = 3600
BATCH = 50

# Cross-tenant, so it runs on the relay identity. A connector with a sync queued
# or running is skipped: the sync will renew the grant itself, and two renewals
# at once collide on the one-active-secret index.
DUE_SQL = f"""
SELECT c.id, c.tenant_id, c.type, c.secret_ref
FROM connector c
WHERE c.status = 'active'
  AND c.type IN ('google_search_console', 'google_analytics')
  AND c.secret_ref IS NOT NULL
  AND (c.last_checked_at IS NULL
       OR c.last_checked_at < now() - interval '{CHECK_AFTER_HOURS} hours')
  AND NOT EXISTS (
    SELECT 1 FROM connector_sync s
    WHERE s.connector_id = c.id AND s.status IN ('queued', 'running')
  )
ORDER BY c.last_checked_at NULLS FIRST
LIMIT $1
"""


@dataclass(frozen=True, slots=True)
class DueGrant:
    tenant_id: UUID
    connector_id: UUID
    connector_type: str
    secret_ref: str


RefresherFactory = Callable[[UUID], Awaitable[TokenRefresher | None]]


async def _record(
    pool: asyncpg.Pool, grant: DueGrant, *, revoked: bool, error_code: str | None
) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await set_tenant(connection, grant.tenant_id)
        if revoked:
            await connection.execute(
                """
                UPDATE connector
                SET status='reauthorization_required', last_error_code=$3
                WHERE id=$1 AND tenant_id=$2 AND status='active'
                """,
                grant.connector_id,
                grant.tenant_id,
                error_code,
            )
            await connection.execute(
                """
                INSERT INTO outbox_event(
                  tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload
                ) VALUES($1,'connector.reauthorization_required',1,'connector',$2,$3::jsonb)
                """,
                grant.tenant_id,
                grant.connector_id,
                json.dumps(
                    {
                        "connector_id": str(grant.connector_id),
                        "connector_type": grant.connector_type,
                        "error_code": error_code,
                    }
                ),
            )
        else:
            await connection.execute(
                "UPDATE connector SET last_error_code=$3 WHERE id=$1 AND tenant_id=$2",
                grant.connector_id,
                grant.tenant_id,
                error_code,
            )


async def check_grant(
    pool: asyncpg.Pool,
    grant: DueGrant,
    *,
    encryption_key: bytes,
    key_version: str,
    refresher_factory: RefresherFactory,
) -> str:
    """Renew one grant. Returns what happened: 'ok', 'revoked', or an error code."""
    manager = AccessTokenManager(
        pool,
        grant,
        encryption_key=encryption_key,
        key_version=key_version,
        refresher=None,
        refresher_factory=refresher_factory,
        expected_scopes=EXPECTED_SCOPES[grant.connector_type],
    )
    try:
        await manager.renew()
    except GoogleAuthorizationRevoked:
        await _record(pool, grant, revoked=True, error_code="authorization_required")
        return "revoked"
    except asyncpg.UniqueViolationError:
        # A sync started between the sweep's query and this renewal, and
        # renewed first. The grant is proven either way.
        return "ok"
    except (GoogleRefreshError, ValueError) as error:
        # Google unavailable, or something wrong on this side. Neither is a
        # reason to ask the tenant to consent again; both are worth showing.
        code = str(error)[:80] or "grant_check_failed"
        await _record(pool, grant, revoked=False, error_code=code)
        return code
    async with pool.acquire() as connection, connection.transaction():
        await set_tenant(connection, grant.tenant_id)
        await connection.execute(
            "UPDATE connector SET last_error_code=NULL WHERE id=$1 AND tenant_id=$2",
            grant.connector_id,
            grant.tenant_id,
        )
    return "ok"


async def check_due_grants(
    relay_pool: asyncpg.Pool,
    pool: asyncpg.Pool,
    *,
    encryption_key: bytes,
    key_version: str,
    refresher_factory: RefresherFactory,
    batch: int = BATCH,
) -> dict[str, int]:
    async with relay_pool.acquire() as connection:
        rows = await connection.fetch(DUE_SQL, batch)
    outcomes: dict[str, int] = {}
    for row in rows:
        grant = DueGrant(
            tenant_id=row["tenant_id"],
            connector_id=row["id"],
            connector_type=str(row["type"]),
            secret_ref=str(row["secret_ref"]),
        )
        try:
            outcome = await check_grant(
                pool,
                grant,
                encryption_key=encryption_key,
                key_version=key_version,
                refresher_factory=refresher_factory,
            )
        except Exception:
            logger.exception("grant check failed for connector %s", grant.connector_id)
            outcome = "unexpected_error"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


async def run_grant_checker(
    relay_pool: asyncpg.Pool,
    pool: asyncpg.Pool,
    *,
    encryption_key: bytes,
    key_version: str,
    refresher_factory: RefresherFactory,
    interval_seconds: int = SWEEP_INTERVAL_SECONDS,
) -> None:
    while True:
        try:
            outcomes = await check_due_grants(
                relay_pool,
                pool,
                encryption_key=encryption_key,
                key_version=key_version,
                refresher_factory=refresher_factory,
            )
            if outcomes:
                logger.info("grant check: %s", outcomes)
        except Exception:
            logger.exception("grant check sweep failed")
        await asyncio.sleep(interval_seconds)
