"""Drafting the changes an opportunity already implies, without a person.

Separation of duties says the author of a proposal cannot approve it. That rule
is right and stays. What it collided with is that drafting was only ever a
person clicking a button, so the clicker became the author -- and in a tenant
with one member, every proposal it produced was unapprovable by construction.
On 2026-09-08 that was twelve verified heading repairs for wordkitapp.com that
nobody in the tenant was permitted to approve.

The fix is not to weaken the rule. It is to notice that these repairs are not
authored by anybody. `h1_repair` cuts the heading out of the page's own
`<title>` by fixed rules and returns the same answer every time; no judgement
is exercised and no person contributed anything but a click. Recording a human
as the author of that was the inaccuracy, and it was the inaccuracy that
blocked the approval.

So the sweep drafts them as itself, and every human in the tenant remains free
to approve or reject. Separation of duties is intact -- it is between the
drafter and the approver, and the drafter is a machine.

**Only for sites that have said yes.** A site in `observe` is being measured,
not changed, so nothing is drafted for it. `recommend` and `autopilot` are the
modes in which an operator has stated that the platform may propose changes,
and that statement is an audited governance decision rather than a setting. It
is the same permission the deployment gate reads, which keeps "what may be
drafted" and "what may be deployed" from drifting apart.

Drafting is still not approving, and this deploys nothing. Every proposal it
creates goes through `create_proposal` exactly as a hand-written one does:
classified by risk, validated, and left waiting for a human.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.domain.github_adapter import GitHubDeploymentError
from app.services.github_connector import GitHubConnectorError, credential_for_opportunity
from app.services.proposal_drafts import (
    LIVE_PROPOSAL_STATUSES,
    REPAIRABLE_RULES,
    ProposalDraftService,
)
from app.services.proposals import ProposalService

logger = logging.getLogger(__name__)

# A fixed, non-personal actor. There is no `app_user` behind it, and a real
# user id here would attribute a machine's deterministic edit to a person --
# which is the precise inaccuracy this exists to correct.
DRAFTER_ACTOR_ID = UUID("019d0000-0000-7000-8000-0000000000a2")

# Distinct from the reconciler's lock: two sweeps that block each other would
# make one of them silently late rather than concurrent.
SWEEP_LOCK_KEY = 0x5E0D_2A17

# The modes in which an operator has said the platform may propose changes.
DRAFTABLE_MODES = ("recommend", "autopilot")

# Bounded per sweep so a newly enabled site cannot turn one tick into hundreds
# of pull-request-shaped intentions nobody asked for in that moment. The
# remainder is drafted on the next tick; there is no deadline here.
DEFAULT_BATCH = 20

# Opportunities that are open, carry a rule this system can repair
# deterministically, belong to a site that has both said yes and has somewhere
# to write, and whose page nobody is already proposing a change to. Ordered by
# score so the most valuable are drafted first when the batch is the binding
# constraint.
DRAFTABLE_SQL = """
SELECT ranked.tenant_id, ranked.opportunity_id, ranked.site_id
FROM (
  SELECT DISTINCT ON (o.page_id)
         o.tenant_id, o.id AS opportunity_id, o.site_id, o.score
  FROM opportunity o
  JOIN site s ON s.id = o.site_id AND s.tenant_id = o.tenant_id
  WHERE o.status = 'open'
    AND s.status = 'active'
    AND s.verified_at IS NOT NULL
    AND s.emergency_freeze = false
    AND s.mode = ANY(:modes)
    AND EXISTS (
      SELECT 1 FROM connector c
      WHERE c.site_id = s.id AND c.tenant_id = s.tenant_id
        AND c.type = 'github_repository' AND c.status = 'active'
    )
    AND EXISTS (
      SELECT 1 FROM opportunity_finding of2
      JOIN finding f ON f.id = of2.finding_id AND f.tenant_id = of2.tenant_id
      WHERE of2.opportunity_id = o.id AND of2.tenant_id = o.tenant_id
        AND f.rule_key = ANY(:rules)
    )
    -- One live proposal per page, not merely per opportunity. Two open
    -- opportunities can name the same page through different rules --
    -- h1.duplicate_across_site and content.title_h1_mismatch both did, on six
    -- wordkitapp.com pages -- and h1_repair answers both with the identical
    -- edit. Guarding only on opportunity_id drafted that edit a second time,
    -- which deploys as two pull requests changing one line to the same thing.
    AND NOT EXISTS (
      SELECT 1 FROM proposal p
      WHERE p.tenant_id = o.tenant_id
        AND p.status = ANY(:live)
        AND (p.opportunity_id = o.id OR p.page_id = o.page_id)
    )
  -- The guard above only settles sweeps after the first. Within one sweep both
  -- rows are selected before either is written, so the batch itself has to hold
  -- the rule: one opportunity per page, the highest-scoring one. Safe to key on
  -- page_id alone because `opportunity.page_id` is NOT NULL; DISTINCT ON treats
  -- NULLs as equal and would fold page-less rows into a single candidate.
  ORDER BY o.page_id, o.score DESC, o.id
) ranked
ORDER BY ranked.score DESC, ranked.opportunity_id
LIMIT :limit
"""


@dataclass(frozen=True, slots=True)
class DraftCandidate:
    tenant_id: UUID
    opportunity_id: UUID
    site_id: UUID


@dataclass(frozen=True, slots=True)
class DraftReport:
    considered: int = 0
    drafted: int = 0
    refused: int = 0
    unavailable: int = 0

    def plus(self, **counts: int) -> DraftReport:
        return DraftReport(
            considered=self.considered + counts.get("considered", 0),
            drafted=self.drafted + counts.get("drafted", 0),
            refused=self.refused + counts.get("refused", 0),
            unavailable=self.unavailable + counts.get("unavailable", 0),
        )


def drafter_context(tenant_id: UUID, trace_id: str) -> TenantContext:
    """The scope this sweep acts under.

    OWNER because `create_proposal` requires a role permitted to author, and
    the sweep acts for the tenant rather than as a member of it. It never
    approves and never deploys: those paths check `actor_id` against the
    author, and this id is the author of everything it makes.
    """
    return TenantContext(
        tenant_id=tenant_id,
        actor_id=DRAFTER_ACTOR_ID,
        role=Role.OWNER,
        trace_id=trace_id,
    )


async def draftable(connection, rules: list[str], limit: int) -> list[DraftCandidate]:
    rows = await connection.execute(
        text(DRAFTABLE_SQL).bindparams(
            modes=list(DRAFTABLE_MODES),
            rules=rules,
            live=list(LIVE_PROPOSAL_STATUSES),
            limit=limit,
        )
    )
    return [
        DraftCandidate(
            tenant_id=row.tenant_id,
            opportunity_id=row.opportunity_id,
            site_id=row.site_id,
        )
        for row in rows.all()
    ]


async def draft_one(
    session: AsyncSession,
    candidate: DraftCandidate,
    settings: Settings,
    client: httpx.AsyncClient,
    trace_id: str,
) -> str:
    """Draft this one opportunity, or say why not.

    Every refusal is a decision the drafter or the policy made on purpose --
    a title that yields no heading, a document with two `<h1>` elements, a
    repository that cannot be read. None of them is a reason to stop the sweep,
    and none of them should be retried differently next tick: the same input
    will produce the same refusal, which is the point of a deterministic repair.
    """
    context = drafter_context(candidate.tenant_id, trace_id)
    credential = await credential_for_opportunity(
        session, context, candidate.opportunity_id, settings, client
    )
    from app.domain.github_adapter import GitHubDeploymentAdapter

    adapter = GitHubDeploymentAdapter(client, credential.target, credential.token)
    site_id, command = await ProposalDraftService(
        session, context, credential.path_template
    ).draft_from_opportunity(candidate.opportunity_id, adapter.read_file)
    proposal = await ProposalService(session, context).create_proposal(site_id, command)
    return str(proposal.id)


async def draft_once(
    connection,
    tenant_session: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    limit: int = DEFAULT_BATCH,
) -> DraftReport:
    candidates = await draftable(connection, sorted(REPAIRABLE_RULES), limit)
    report = DraftReport(considered=len(candidates))
    for candidate in candidates:
        trace_id = f"drafter-{candidate.opportunity_id.hex[:12]}"
        try:
            async with tenant_session(candidate.tenant_id) as session:
                proposal_id = await draft_one(
                    session, candidate, settings, client, trace_id
                )
                await session.commit()
            logger.info(
                "drafted a proposal",
                extra={"opportunity_id": str(candidate.opportunity_id), "proposal_id": proposal_id},
            )
            report = report.plus(drafted=1)
        except HTTPException as refusal:
            # A decision, not a fault. Recorded at debug: on a site with a
            # standing unrepairable opportunity this would otherwise log the
            # same line every tick for ever.
            logger.debug(
                "opportunity not drafted",
                extra={
                    "opportunity_id": str(candidate.opportunity_id),
                    "detail": str(refusal.detail),
                },
            )
            report = report.plus(refused=1)
        except (GitHubConnectorError, GitHubDeploymentError, httpx.HTTPError) as error:
            # The repository could not be read. Transient or not, it is not this
            # opportunity's fault and it will be reconsidered next tick.
            logger.warning(
                "could not read the repository for an opportunity",
                extra={
                    "opportunity_id": str(candidate.opportunity_id),
                    "error": error.__class__.__name__,
                },
            )
            report = report.plus(unavailable=1)
    return report


async def run_drafting_sweep(
    relay_engine: AsyncEngine,
    tenant_session: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    interval_seconds: int,
    limit: int = DEFAULT_BATCH,
) -> None:
    """Poll for as long as the process lives, one sweeper at a time.

    An advisory lock rather than a schedule: several API processes may run, and
    two of them drafting the same opportunity would race on the "already
    proposed" check and produce a duplicate that a human then has to reject.
    """
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
                    report = await draft_once(
                        connection, tenant_session, settings, client, limit=limit
                    )
                    if report.drafted or report.unavailable:
                        logger.info(
                            "drafting sweep",
                            extra={
                                "considered": report.considered,
                                "drafted": report.drafted,
                                "refused": report.refused,
                                "unavailable": report.unavailable,
                            },
                        )
                finally:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_LOCK_KEY}
                    )
        except Exception:
            # A sweep that dies takes drafting with it until the next restart,
            # which is exactly the quiet failure this system keeps finding. Log
            # and continue.
            logger.exception("drafting sweep failed")
        await asyncio.sleep(interval_seconds)
