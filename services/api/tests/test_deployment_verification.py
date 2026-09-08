"""What the verifier concludes, and what it refuses to conclude.

The distinction this sweep exists to keep is between "the change is not on the
page" and "nobody answered the door". Collapsing them would let a DNS blip or a
timeout be recorded as a deployment that failed to land, which is a claim about
somebody's website made from no evidence at all.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.context import Role
from app.services.deployment_verification import (
    SWEEP_LOCK_KEY,
    VERIFIER_ACTOR_ID,
    VerificationCandidate,
    VerifyReport,
    belongs_to_site,
    verifier_context,
    verify_once,
)
from app.services.proposal_drafting import DRAFTER_ACTOR_ID
from app.services.proposal_drafting import SWEEP_LOCK_KEY as DRAFT_LOCK_KEY
from app.services.rollback_reconciliation import RECONCILER_ACTOR_ID
from app.services.rollback_reconciliation import SWEEP_LOCK_KEY as RECONCILE_LOCK_KEY

pytestmark = pytest.mark.asyncio


TENANT = uuid4()


def candidate(url: str = "https://wordkitapp.com/rhyme-tool", host: str = "wordkitapp.com"):
    return VerificationCandidate(
        tenant_id=TENANT, proposal_id=uuid4(), url=url, host=host
    )


class FakeSessions:
    def __init__(self) -> None:
        self.session = AsyncMock()
        self.committed = 0
        self.session.commit = AsyncMock(side_effect=self._commit)

    def _commit(self) -> None:
        self.committed += 1

    @asynccontextmanager
    async def _scope(self, tenant_id: UUID) -> AsyncIterator[AsyncMock]:
        yield self.session

    def __call__(self, tenant_id: UUID):
        return self._scope(tenant_id)


async def sweep(candidates, verify_one_impl):
    sessions = FakeSessions()
    with (
        patch(
            "app.services.deployment_verification.unverified",
            new=AsyncMock(return_value=candidates),
        ),
        patch("app.services.deployment_verification.verify_one", new=verify_one_impl),
    ):
        report = await verify_once(AsyncMock(), sessions, MagicMock(), MagicMock())
    return report, sessions


async def test_the_verifier_is_nobody_and_is_its_own_nobody() -> None:
    """Three machine identities, three jobs, three ids.

    An audit trail that cannot say which sweep wrote a row is not much of an
    audit trail, and a shared lock would make one of them silently late.
    """
    context = verifier_context(TENANT, "t")
    assert context.actor_id == VERIFIER_ACTOR_ID
    assert context.tenant_id == TENANT
    assert context.role is Role.OWNER

    assert len({VERIFIER_ACTOR_ID, DRAFTER_ACTOR_ID, RECONCILER_ACTOR_ID}) == 3
    assert len({SWEEP_LOCK_KEY, DRAFT_LOCK_KEY, RECONCILE_LOCK_KEY}) == 3


async def test_a_change_found_on_the_page_is_verified() -> None:
    report, sessions = await sweep([candidate(), candidate()], AsyncMock(return_value=True))
    assert report == VerifyReport(considered=2, verified=2)
    assert sessions.committed == 2


async def test_a_change_absent_from_the_page_is_not_an_error() -> None:
    """The ordinary state of a change deployed minutes ago.

    The pull request is open and nobody has merged it. That is worth recording
    and is not a fault, so the sweep keeps going and will look again next tick.
    """
    report, _ = await sweep([candidate(), candidate()], AsyncMock(return_value=False))
    assert report == VerifyReport(considered=2, not_live=2)


async def test_a_site_that_does_not_answer_is_not_recorded_as_not_live() -> None:
    """The distinction the whole sweep turns on.

    "The heading is not on the page" is a finding about the change. "The
    request timed out" is a finding about the network. Counting the second as
    the first would report somebody's deployment as having failed on the
    evidence of nothing.
    """
    for error in (httpx.ConnectError("nope"), httpx.ReadTimeout("nope")):
        report, _ = await sweep([candidate()], AsyncMock(side_effect=error))
        assert report == VerifyReport(considered=1, unreachable=1), error
        assert report.not_live == 0


async def test_one_refusal_does_not_stop_the_sweep() -> None:
    outcomes = [HTTPException(status_code=422, detail="proposal_has_no_deployment_receipt"), True]

    async def verifying(*_args, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    report, _ = await sweep([candidate(), candidate()], verifying)
    assert report == VerifyReport(considered=2, verified=1, unreachable=1)


async def test_an_unexpected_failure_is_not_swallowed() -> None:
    with pytest.raises(RuntimeError, match="something else"):
        await sweep([candidate()], AsyncMock(side_effect=RuntimeError("something else")))


async def test_only_the_sites_own_pages_over_http_are_fetched() -> None:
    """This runs inside the API, on the private network.

    The URL comes from the database rather than from a request, so this is not
    the front line -- but a page row that ever came to hold an internal address
    would otherwise turn the verifier into a fetcher for it.
    """
    assert belongs_to_site("https://wordkitapp.com/rhyme-tool", "wordkitapp.com")
    assert belongs_to_site("http://wordkitapp.com/rhyme-tool", "wordkitapp.com")
    for url in (
        "https://evil.example/rhyme-tool",
        "https://wordkitapp.com.evil.example/x",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/v1/sites",
    ):
        assert not belongs_to_site(url, "wordkitapp.com"), url


async def test_a_page_off_its_own_site_is_never_fetched_at_all() -> None:
    """Counted as unreachable, and `verify_one` is not reached."""
    verifying = AsyncMock(return_value=True)
    report, _ = await sweep([candidate(url="https://elsewhere.example/x")], verifying)
    assert report == VerifyReport(considered=1, unreachable=1)
    verifying.assert_not_awaited()
