"""Asking the live site whether a deployed change is actually there.

Deploying opens a pull request. The adapter has never had merge authority, so
nothing is live until a person merges -- and until 2026-09-08 nothing ever went
and looked. A receipt said `applied` the moment the pull request existed and
said `applied` for ever after, whether it was merged that afternoon or closed
unread.

That gap sat directly under the measurement story. `calculate_or_get_measurement`
refuses without a `verified` PostDeployVerification, and the only thing that
creates one is `verify_deployment`, which needs the live page's HTML. Nothing
fetched it. So no measurement could ever be computed, for any change, on any
site, however long anybody waited -- the 28-day window was irrelevant because
the gate before it was shut. The dashboard's "No measurement series recorded
yet" was not a young system; it was a loop with no beginning.

This sweep is that beginning. For every deployed proposal not yet verified it
fetches the page and records what it saw. Two answers, both worth having:

* **verified** -- the added lines are on the live page. The change landed, the
  receipt is stamped, and the 28-day measurement can eventually be computed.
* **failed** -- they are not. Usually that means nobody has merged the pull
  request yet, which is the ordinary state of a change deployed minutes ago;
  sometimes it means a later, better change overwrote the same lines. From the
  page alone those are indistinguishable, so the record says both and claims
  neither. It is re-checked while the deployment is recent, so a merge tomorrow
  is picked up tomorrow, and then left alone.

It only ever reads. It cannot merge, deploy, revert, or change a proposal's
status; the worst it can do is record that it could not find something.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.services.measurements import MeasurementService

logger = logging.getLogger(__name__)

# A fixed, non-personal actor, distinct from the drafter and the reconciler.
# Three machine identities doing three different jobs; an audit trail that
# cannot tell them apart is not much of an audit trail.
VERIFIER_ACTOR_ID = UUID("019d0000-0000-7000-8000-0000000000a3")

# Its own lock, for the same reason the others have theirs: two sweeps sharing
# one would make whichever lost the race silently late rather than concurrent.
SWEEP_LOCK_KEY = 0x5E0D_2A18

DEFAULT_BATCH = 20

# How large a page may be before this refuses to read it. A verification target
# is an HTML document; anything past this is not one, and holding it in memory
# to run a substring search over it helps nobody.
MAX_BODY_BYTES = 4 * 1024 * 1024

# How long a change that is not on the page keeps being asked about. A pull
# request nobody has merged in two weeks is not about to be, and the other
# reason for failing -- a change since superseded by a better one -- is
# permanent. Without this the sweep re-fetches those pages every tick for ever:
# on 2026-09-08 that was sixteen thecalchive.com pages, four times an hour,
# indefinitely, to re-learn something already written down.
RETRY_WINDOW_DAYS = 14

# Deployed proposals whose change nobody has confirmed is live.
#
# A verification that already succeeded is final -- the change was observed on
# the page, and re-reading it later would only let a subsequent unrelated edit
# retract a fact that was true when it was recorded.
#
# One never looked at is always looked at, however old, so enabling this sweep
# reaches the whole backlog once. One that failed is retried only while the
# deployment is recent, because the usual reason for failure is a pull request
# still waiting to be merged -- and that reason expires.
UNVERIFIED_SQL = """
SELECT p.tenant_id, p.id AS proposal_id, pg.normalized_url, s.normalized_host
FROM proposal p
JOIN deployment_receipt r ON r.proposal_id = p.id AND r.tenant_id = p.tenant_id
JOIN site s ON s.id = p.site_id AND s.tenant_id = p.tenant_id
JOIN page pg ON pg.id = p.page_id AND pg.tenant_id = p.tenant_id
WHERE p.status = 'deployed'
  AND s.status = 'active'
  AND NOT EXISTS (
    SELECT 1 FROM post_deploy_verification v
    WHERE v.proposal_id = p.id AND v.tenant_id = p.tenant_id
      AND v.status = 'verified'
  )
  AND (
    NOT EXISTS (
      SELECT 1 FROM post_deploy_verification v
      WHERE v.proposal_id = p.id AND v.tenant_id = p.tenant_id
    )
    OR r.deployed_at > now() - make_interval(days => :retry_days)
  )
ORDER BY r.deployed_at
LIMIT :limit
"""


@dataclass(frozen=True, slots=True)
class VerificationCandidate:
    tenant_id: UUID
    proposal_id: UUID
    url: str
    host: str


@dataclass(frozen=True, slots=True)
class VerifyReport:
    considered: int = 0
    verified: int = 0
    not_live: int = 0
    unreachable: int = 0

    def plus(self, **counts: int) -> VerifyReport:
        return VerifyReport(
            considered=self.considered + counts.get("considered", 0),
            verified=self.verified + counts.get("verified", 0),
            not_live=self.not_live + counts.get("not_live", 0),
            unreachable=self.unreachable + counts.get("unreachable", 0),
        )


def verifier_context(tenant_id: UUID, trace_id: str) -> TenantContext:
    """The scope this sweep acts under.

    OWNER because recording a verification writes an audit event for the tenant.
    It authors no proposal, approves nothing and deploys nothing: the only row
    it creates says what a public URL returned.
    """
    return TenantContext(
        tenant_id=tenant_id,
        actor_id=VERIFIER_ACTOR_ID,
        role=Role.OWNER,
        trace_id=trace_id,
    )


def belongs_to_site(url: str, host: str) -> bool:
    """Only ever fetch the site's own pages, over http(s).

    The URL comes from the database rather than from a request, so this is not
    the front line. It is still the check worth having: this runs inside the
    API, on the private network, and a page row that ever came to hold an
    internal address would otherwise make the verifier fetch it. Nothing should
    be reachable through here that the crawler could not already reach.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    return parts.hostname is not None and parts.hostname.lower() == host.lower()


async def unverified(connection, limit: int) -> list[VerificationCandidate]:
    rows = await connection.execute(
        text(UNVERIFIED_SQL).bindparams(limit=limit, retry_days=RETRY_WINDOW_DAYS)
    )
    return [
        VerificationCandidate(
            tenant_id=row.tenant_id,
            proposal_id=row.proposal_id,
            url=row.normalized_url,
            host=row.normalized_host,
        )
        for row in rows.all()
    ]


async def fetch_live(client: httpx.AsyncClient, url: str) -> tuple[str, int]:
    """The page as a visitor would get it, redirects followed.

    A 4xx or 5xx is returned rather than raised: "the page 404s" is a real
    verification outcome and belongs in the record, not in an exception.
    """
    response = await client.get(url, follow_redirects=True)
    body = response.text
    if len(body.encode("utf-8", "ignore")) > MAX_BODY_BYTES:
        body = body[: MAX_BODY_BYTES // 2]
    return body, response.status_code


async def verify_one(
    session: AsyncSession,
    candidate: VerificationCandidate,
    client: httpx.AsyncClient,
    trace_id: str,
) -> bool:
    context = verifier_context(candidate.tenant_id, trace_id)
    body, status_code = await fetch_live(client, candidate.url)
    verification = await MeasurementService(session, context).verify_deployment(
        candidate.proposal_id, live_body=body, live_status=status_code
    )
    return verification.status == "verified"


async def verify_once(
    connection,
    tenant_session: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    limit: int = DEFAULT_BATCH,
) -> VerifyReport:
    candidates = await unverified(connection, limit)
    report = VerifyReport(considered=len(candidates))
    for candidate in candidates:
        trace_id = f"verifier-{candidate.proposal_id.hex[:12]}"
        if not belongs_to_site(candidate.url, candidate.host):
            logger.warning(
                "refusing to verify a page that is not on its own site",
                extra={"proposal_id": str(candidate.proposal_id), "host": candidate.host},
            )
            report = report.plus(unreachable=1)
            continue
        try:
            async with tenant_session(candidate.tenant_id) as session:
                landed = await verify_one(session, candidate, client, trace_id)
                await session.commit()
            report = report.plus(**{"verified" if landed else "not_live": 1})
        except HTTPException as refusal:
            # The proposal lost its receipt, or the evidence was rejected. A
            # decision about this one candidate, not a reason to stop.
            logger.debug(
                "verification refused",
                extra={
                    "proposal_id": str(candidate.proposal_id),
                    "detail": str(refusal.detail),
                },
            )
            report = report.plus(unreachable=1)
        except httpx.HTTPError as error:
            # The site did not answer. Nothing is concluded about the change --
            # in particular it is *not* recorded as absent, which would be a
            # claim this sweep has no evidence for.
            logger.warning(
                "could not reach a page to verify it",
                extra={
                    "proposal_id": str(candidate.proposal_id),
                    "error": error.__class__.__name__,
                },
            )
            report = report.plus(unreachable=1)
    return report


async def run_verification_sweep(
    relay_engine: AsyncEngine,
    tenant_session: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    interval_seconds: int,
    limit: int = DEFAULT_BATCH,
) -> None:
    """Poll for as long as the process lives, one sweeper at a time."""
    while True:
        try:
            async with relay_engine.connect() as connection:
                held = await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": SWEEP_LOCK_KEY}
                )
                if not held:
                    await asyncio.sleep(interval_seconds)
                    continue
                try:
                    report = await verify_once(
                        connection, tenant_session, settings, client, limit=limit
                    )
                    if report.verified or report.unreachable:
                        logger.info(
                            "verification sweep",
                            extra={
                                "considered": report.considered,
                                "verified": report.verified,
                                "not_live": report.not_live,
                                "unreachable": report.unreachable,
                            },
                        )
                finally:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_LOCK_KEY}
                    )
        except Exception:
            # A sweep that dies takes verification with it until the next
            # restart, and its absence looks exactly like "nothing is merged
            # yet". Log and continue.
            logger.exception("verification sweep failed")
        await asyncio.sleep(interval_seconds)
