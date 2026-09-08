"""Who the drafter is, and what that makes possible.

The point of this sweep is not convenience. It is that a deterministic repair
has no human author, and recording one blocked every approval in a tenant with
a single member.

What these tests can check is the shape: who the drafter is, that its id is
fixed and belongs to no person, and that the query only reaches sites which
have said the platform may propose changes. That a real signed-in person can
then approve what it drafted is a property of the running system, and was
verified there rather than asserted here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.context import Role
from app.domain.github_adapter import GitHubDeploymentError
from app.services.github_connector import GitHubConnectorError
from app.services.proposal_drafting import (
    DRAFTER_ACTOR_ID,
    SWEEP_LOCK_KEY,
    DraftCandidate,
    DraftReport,
    draft_once,
    drafter_context,
)
from app.services.rollback_reconciliation import SWEEP_LOCK_KEY as RECONCILE_LOCK_KEY

pytestmark = pytest.mark.asyncio

TENANT = uuid4()


def candidate() -> DraftCandidate:
    return DraftCandidate(tenant_id=TENANT, opportunity_id=uuid4(), site_id=uuid4())


class FakeSessions:
    """A tenant-scoped session factory that records commits.

    Only a committed draft survives, so counting commits is how these tests
    tell "drafted" apart from "attempted".
    """

    def __init__(self) -> None:
        self.session = AsyncMock()
        self.committed = 0
        self.tenants: list[UUID] = []
        self.session.commit = AsyncMock(side_effect=self._commit)

    def _commit(self) -> None:
        self.committed += 1

    @asynccontextmanager
    async def _scope(self, tenant_id: UUID) -> AsyncIterator[AsyncMock]:
        self.tenants.append(tenant_id)
        yield self.session

    def __call__(self, tenant_id: UUID):
        return self._scope(tenant_id)


async def sweep(candidates, draft_one_impl):
    connection = AsyncMock()
    sessions = FakeSessions()
    with (
        patch("app.services.proposal_drafting.draftable", new=AsyncMock(return_value=candidates)),
        patch("app.services.proposal_drafting.draft_one", new=draft_one_impl),
    ):
        report = await draft_once(connection, sessions, MagicMock(), MagicMock())
    return report, sessions


async def test_the_drafter_is_nobody() -> None:
    """A real user id here would attribute a machine's edit to a person.

    That attribution is the whole defect: it is what made twelve verified
    heading repairs unapprovable by the only member of the tenant.
    """
    context = drafter_context(TENANT, "t")
    assert context.actor_id == DRAFTER_ACTOR_ID
    assert context.tenant_id == TENANT
    # Author-capable, because it authors. It never approves or deploys.
    assert context.role is Role.OWNER


async def test_the_two_sweeps_do_not_share_a_lock() -> None:
    """Sharing one would make a sweep silently late rather than concurrent."""
    assert SWEEP_LOCK_KEY != RECONCILE_LOCK_KEY


async def test_each_drafted_opportunity_is_committed() -> None:
    candidates = [candidate(), candidate()]
    report, sessions = await sweep(candidates, AsyncMock(return_value=str(uuid4())))
    assert report == DraftReport(considered=2, drafted=2)
    assert sessions.committed == 2


async def test_a_refusal_does_not_stop_the_sweep() -> None:
    """A title that yields no heading is a decision, not an outage.

    The next candidate is somebody else's page and has nothing to do with it.
    """
    candidates = [candidate(), candidate(), candidate()]
    refusal = HTTPException(status_code=409, detail="h1_repair_title_yields_no_heading")
    results = [refusal, str(uuid4()), refusal]

    async def drafting(*_args, **_kwargs):
        outcome = results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    report, _ = await sweep(candidates, drafting)
    assert report == DraftReport(considered=3, drafted=1, refused=2)


async def test_a_repository_that_cannot_be_read_is_counted_apart_from_a_refusal() -> None:
    """One will produce the same answer next tick; the other may not.

    Collapsing them would hide a repository whose token expired inside a count
    of pages that legitimately cannot be repaired.
    """
    for error in (
        GitHubConnectorError("nope"),
        GitHubDeploymentError("nope"),
        httpx.ConnectError("nope"),
    ):
        report, _ = await sweep([candidate()], AsyncMock(side_effect=error))
        assert report == DraftReport(considered=1, unavailable=1), error


async def test_an_unexpected_failure_is_not_swallowed() -> None:
    """Only refusals and repository trouble are absorbed.

    A bug in the repair would otherwise be counted as a page that could not be
    repaired, every tick, for ever.
    """
    with pytest.raises(RuntimeError, match="something else"):
        await sweep([candidate()], AsyncMock(side_effect=RuntimeError("something else")))


async def test_only_permitted_sites_and_repairable_rules_are_selected() -> None:
    """The query is the gate, so assert what it actually restricts.

    A site in `observe` is being measured, not changed. Reading the same
    permission the deployment gate reads keeps "may be drafted" and "may be
    deployed" from drifting apart.
    """
    from app.services.proposal_drafting import DRAFTABLE_MODES, DRAFTABLE_SQL

    assert DRAFTABLE_MODES == ("recommend", "autopilot")
    assert "observe" not in DRAFTABLE_SQL
    for clause in (
        "s.verified_at IS NOT NULL",
        "s.emergency_freeze = false",
        "c.type = 'github_repository'",
        "f.rule_key = ANY(:rules)",
        "NOT EXISTS",
        # One candidate per page, not per opportunity -- both halves of it.
        # What these actually do to the rows returned is asserted against
        # PostgreSQL in tests/integration/test_drafting_selection.py; a clause
        # being present in a string cannot show that.
        "p.opportunity_id = o.id OR p.page_id = o.page_id",
        "DISTINCT ON (o.page_id)",
        "ORDER BY ranked.score DESC",
    ):
        assert clause in DRAFTABLE_SQL, clause


async def test_the_drafter_is_not_a_member_and_so_is_never_the_approver() -> None:
    """The reason this exists, stated as narrowly as a unit test can.

    `author_cannot_approve_own_proposal` compares the approver's actor id with
    the proposal's author. A person drafting their own change collides with it;
    the drafter cannot, because its id is a fixed sentinel that no `app_user`
    row carries and nothing can sign in as.

    This asserts the shape only. That a real signed-in person can approve what
    the sweep drafted is a property of the running system, and was verified
    there rather than claimed here.
    """
    human = uuid4()
    assert DRAFTER_ACTOR_ID != human

    # The sentinel is fixed, not generated: a drafter whose id changed per
    # process could eventually collide with a user id, and the collision would
    # surface as an approval refused for no visible reason.
    from app.services.proposal_drafting import drafter_context as fresh

    assert fresh(TENANT, "a").actor_id == fresh(uuid4(), "b").actor_id == DRAFTER_ACTOR_ID

    # Distinct from the reconciler's actor for the same reason the locks are
    # distinct: two machine identities doing different jobs should be
    # separable in an audit trail.
    from app.services.rollback_reconciliation import RECONCILER_ACTOR_ID

    assert DRAFTER_ACTOR_ID != RECONCILER_ACTOR_ID
