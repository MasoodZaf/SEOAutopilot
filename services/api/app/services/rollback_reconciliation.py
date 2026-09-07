"""Finding out what happened to a revert pull request.

Rolling back a *merged* deployment does not undo anything by itself. The GitHub
adapter opens a revert pull request and stops -- it has never had merge
authority, and rollback is not the moment to grant it -- so the deployed content
stays live until a person merges. Migration 0033 gave that state its own name
because the receipt used to claim the change was reversed when it was not.

What 0033 could not do is find out what happened next. Nothing watched the
revert, so a rollback stayed `rollback_pending` for ever: the pilot's
emi-calculator revert was closed unmerged on 2026-09-06 and the records still
said an undo was in progress. This is the part that closes it.

Three outcomes, and each one means something different about the site:

* **merged** -- the change really is undone. This is the only path to
  `rolled_back`, and it is reached by a person merging, which is the whole point.
* **closed without merging** -- somebody decided not to undo it. The deployed
  change is still live and the proposal is still deployed; the *rollback*
  failed, and saying so is more useful than leaving it pending for ever.
* **still open** -- nothing has happened. Recorded as checked, not as resolved.

Anything else -- a repository that cannot be read, a pull request that no longer
exists -- leaves the receipt pending and records why. A transient permission
error must not be able to close a rollback out.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, DeploymentReceipt, OutboxEvent, Proposal, RollbackReceipt
from app.services.github_connector import GitHubConnectorError, credential_for_site
from app.services.sites import stable_hash

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
PULL_URL = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)/?$")

# The actor recorded for a transition nobody performed interactively. It is not
# a user id: no person made this change, a poll observed one.
SYSTEM_ACTOR = "reconciler"

# A fixed, non-personal actor for the tenant scope the sweep acts under. It
# names nobody on purpose -- there is no `app_user` behind it, and a real user
# id here would attribute a machine's observation to a person.
RECONCILER_ACTOR_ID = UUID("019d0000-0000-7000-8000-0000000000a1")


@dataclass(frozen=True, slots=True)
class PendingRollback:
    tenant_id: UUID
    rollback_id: UUID
    site_id: UUID
    proposal_id: UUID
    deployment_receipt_id: UUID
    external_ref: str


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    checked: int = 0
    merged: int = 0
    abandoned: int = 0
    still_open: int = 0
    unresolved: int = 0

    def plus(self, **counts: int) -> ReconcileReport:
        return ReconcileReport(
            checked=self.checked + counts.get("checked", 0),
            merged=self.merged + counts.get("merged", 0),
            abandoned=self.abandoned + counts.get("abandoned", 0),
            still_open=self.still_open + counts.get("still_open", 0),
            unresolved=self.unresolved + counts.get("unresolved", 0),
        )


def parse_pull_request(external_ref: str) -> tuple[str, int] | None:
    """The repository and number a revert pull request URL names.

    The adapter stores the pull request's `html_url`, whose shape is a stable
    part of GitHub's interface. Reading the repository out of it rather than
    from the connector matters: the revert lives wherever it was opened, and a
    site whose connector was later repointed must not have this poll ask a
    different repository about that number.
    """
    match = PULL_URL.match(external_ref.strip())
    if match is None:
        return None
    return match.group(1), int(match.group(2))


async def pending_rollbacks(connection, limit: int = 100) -> list[PendingRollback]:
    """Every unfinished rollback, across tenants, oldest check first.

    This one statement cannot be tenant scoped, because its job is to find work
    in tenants nobody is currently acting for. It runs on the relay identity --
    the role migration 0027 created for exactly these sweeps -- and everything
    it returns is then handled under that row's own tenant scope.
    """
    rows = await connection.execute(
        text(
            """
            SELECT r.tenant_id, r.id, r.site_id, r.proposal_id,
                   r.deployment_receipt_id, r.external_ref
            FROM rollback_receipt r
            WHERE r.status = 'pending' AND coalesce(r.external_ref, '') <> ''
            ORDER BY r.reconciled_at NULLS FIRST, r.rolled_back_at
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [
        PendingRollback(
            tenant_id=row[0],
            rollback_id=row[1],
            site_id=row[2],
            proposal_id=row[3],
            deployment_receipt_id=row[4],
            external_ref=row[5] or "",
        )
        for row in rows.all()
    ]


class RollbackReconciler:
    """Applies to one tenant's records what GitHub says about its reverts."""

    def __init__(self, session: AsyncSession, settings: Settings, client: httpx.AsyncClient) -> None:
        self.session = session
        self.settings = settings
        self.client = client

    def _context(self, tenant_id: UUID) -> TenantContext:
        # Owner, because this writes governance records; the scope is the tenant
        # whose row is being reconciled and nothing else. There is no person
        # here, which is why the audit rows below say `actor_type='system'`.
        return TenantContext(
            tenant_id=tenant_id,
            actor_id=RECONCILER_ACTOR_ID,
            role=Role.OWNER,
            trace_id=f"reconcile-{tenant_id}",
        )

    async def reconcile(self, pending: PendingRollback) -> str:
        rollback = await self.session.scalar(
            select(RollbackReceipt).where(
                RollbackReceipt.id == pending.rollback_id,
                RollbackReceipt.tenant_id == pending.tenant_id,
                RollbackReceipt.status == "pending",
            )
        )
        if rollback is None:
            # Somebody resolved it between the sweep and now.
            return "still_open"

        now = datetime.now(UTC)
        rollback.reconciled_at = now
        target = parse_pull_request(pending.external_ref)
        if target is None:
            rollback.reconcile_error = "external_ref_is_not_a_pull_request_url"
            return "unresolved"
        slug, number = target

        try:
            credential = await credential_for_site(
                self.session,
                self._context(pending.tenant_id),
                pending.site_id,
                self.settings,
                self.client,
            )
        except (GitHubConnectorError, HTTPException, httpx.HTTPError, ValueError) as error:
            # A site whose connector was removed or whose token expired cannot
            # be reconciled. Leaving it pending is right: the revert may still
            # be open, and there is no evidence either way.
            rollback.reconcile_error = f"credential_unavailable:{type(error).__name__}"
            return "unresolved"

        try:
            response = await self.client.get(
                f"{GITHUB_API}/repos/{slug}/pulls/{number}",
                headers={
                    "Authorization": f"Bearer {credential.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.HTTPError as error:
            rollback.reconcile_error = f"github_unreachable:{type(error).__name__}"
            return "unresolved"

        if response.status_code != 200:
            rollback.reconcile_error = f"github_pull_read_failed:{response.status_code}"
            return "unresolved"

        pull = response.json()
        rollback.reconcile_error = None
        if pull.get("merged_at"):
            await self._reversed(pending, rollback, now, str(pull.get("merged_at")))
            return "merged"
        if pull.get("state") == "closed":
            await self._abandoned(pending, rollback, now)
            return "abandoned"
        return "still_open"

    async def _reversed(
        self, pending: PendingRollback, rollback: RollbackReceipt, now: datetime, merged_at: str
    ) -> None:
        """The revert merged, so the change really is undone."""
        rollback.status = "applied"
        receipt = await self._receipt(pending)
        if receipt is not None:
            receipt.status = "rolled_back"
        proposal = await self._proposal(pending)
        if proposal is not None:
            proposal.status = "failed"
            proposal.updated_at = now
        self._record(
            pending,
            "proposal.rolled_back",
            {
                "detail": "revert_pull_request_merged",
                "merged_at": merged_at,
                "change_reversed": True,
                "receipt_status": "rolled_back",
            },
        )

    async def _abandoned(
        self, pending: PendingRollback, rollback: RollbackReceipt, now: datetime
    ) -> None:
        """The revert was closed without merging, so nothing was undone.

        The deployment goes back to `applied`, because it is: the content is
        live and no undo is outstanding. Recording the rollback as failed is the
        honest end state -- somebody looked at the revert and decided against it.
        """
        rollback.status = "failed"
        receipt = await self._receipt(pending)
        if receipt is not None:
            receipt.status = "applied"
        self._record(
            pending,
            "proposal.rollback_abandoned",
            {
                "detail": "revert_pull_request_closed_without_merging",
                "change_reversed": False,
                "receipt_status": "applied",
            },
        )

    async def _receipt(self, pending: PendingRollback) -> DeploymentReceipt | None:
        return await self.session.scalar(
            select(DeploymentReceipt).where(
                DeploymentReceipt.id == pending.deployment_receipt_id,
                DeploymentReceipt.tenant_id == pending.tenant_id,
            )
        )

    async def _proposal(self, pending: PendingRollback) -> Proposal | None:
        return await self.session.scalar(
            select(Proposal).where(
                Proposal.id == pending.proposal_id,
                Proposal.tenant_id == pending.tenant_id,
            )
        )

    def _record(self, pending: PendingRollback, action: str, extra: dict[str, object]) -> None:
        payload = {
            "proposal_id": str(pending.proposal_id),
            "site_id": str(pending.site_id),
            "deployment_receipt_id": str(pending.deployment_receipt_id),
            "external_ref": pending.external_ref,
            **extra,
        }
        self.session.add(
            AuditEvent(
                tenant_id=pending.tenant_id,
                # No person did this. A poll observed a state change somebody
                # else made on GitHub, and the audit trail should not imply an
                # operator was at a keyboard.
                actor_type="system",
                actor_id=SYSTEM_ACTOR,
                action=action,
                resource_type="proposal",
                resource_id=str(pending.proposal_id),
                trace_id=f"reconcile-{pending.rollback_id}",
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": SYSTEM_ACTOR}),
            )
        )
        self.session.add(
            OutboxEvent(
                tenant_id=pending.tenant_id,
                event_type=f"{action}.v1",
                event_version=1,
                aggregate_type="proposal",
                aggregate_id=pending.proposal_id,
                payload=payload,
            )
        )


# One advisory lock, so two API processes cannot both sweep. The number is
# arbitrary but fixed; it only has to be unique among this application's locks.
SWEEP_LOCK_KEY = 0x5E0A_0001


async def reconcile_once(
    relay_connection,
    tenant_session: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    limit: int = 100,
) -> ReconcileReport:
    """One pass: find unfinished rollbacks, then settle each in its own tenant.

    The two halves use two identities on purpose. Finding work spans tenants and
    so cannot be tenant scoped; settling it is a write to one tenant's records
    and must be. Passing both in rather than reaching for them keeps this
    testable against a real database without a running application.
    """
    report = ReconcileReport()
    for pending in await pending_rollbacks(relay_connection, limit):
        try:
            async with tenant_session(pending.tenant_id) as session:
                outcome = await RollbackReconciler(session, settings, client).reconcile(pending)
        except Exception:
            # One tenant's broken connector must not stop the sweep for every
            # other tenant. The receipt stays pending and is retried next pass.
            logger.exception(
                "rollback reconciliation failed",
                extra={"tenant_id": str(pending.tenant_id), "rollback_id": str(pending.rollback_id)},
            )
            report = report.plus(checked=1, unresolved=1)
            continue
        report = report.plus(checked=1, **{outcome: 1})
    return report


async def run_reconcile_sweep(
    relay_engine: AsyncEngine,
    tenant_session: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    interval_seconds: int,
) -> None:
    """Poll for as long as the process lives, one sweeper at a time.

    A revert is merged by a person, so nothing pushes that fact here and the
    only options are a poll or a webhook. A poll needs no inbound endpoint, no
    per-connector shared secret and no public exposure, and the volume it is
    polling is a handful of open reverts.
    """
    while True:
        try:
            async with relay_engine.connect() as connection:
                held = await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": SWEEP_LOCK_KEY}
                )
                if not held:
                    # Another process is sweeping. Not an error.
                    await asyncio.sleep(interval_seconds)
                    continue
                try:
                    report = await reconcile_once(connection, tenant_session, settings, client)
                    if report.checked:
                        logger.info(
                            "reconciled rollbacks",
                            extra={
                                "checked": report.checked,
                                "merged": report.merged,
                                "abandoned": report.abandoned,
                                "still_open": report.still_open,
                                "unresolved": report.unresolved,
                            },
                        )
                finally:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_LOCK_KEY}
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("rollback reconciliation sweep failed")
        await asyncio.sleep(interval_seconds)
