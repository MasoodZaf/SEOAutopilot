"""The two-person rule on medium and high risk proposals.

Every other proposal test drives an `AsyncMock` session, which returns whatever
the test stages and so cannot see how a real session behaves. That hid a defect:
the service counted prior approvals *after* `session.add(...)`, and SQLAlchemy
autoflushes a pending row into the next query on the same table. The pending
approval came back in the result and was then counted a second time by a `+ 1`,
so the first approver alone satisfied `required_approver_count = 2`.

`AutoflushingSession` below reproduces that flush, so a reintroduction of the
old ordering fails here instead of shipping.
"""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ProposalApprovalCreate
from app.core.context import Role, TenantContext
from app.db.models import Proposal, ProposalApproval
from app.services.proposals import ProposalService


class AutoflushingSession:
    """Flushes staged rows before answering a query, the way a real session does."""

    def __init__(self, proposal: Proposal, stored: list[ProposalApproval] | None = None) -> None:
        self.proposal = proposal
        self.stored: list[ProposalApproval] = list(stored or [])
        self.pending: list[object] = []
        self.flush = AsyncMock()
        self.refresh = AsyncMock()

    def add(self, instance: object) -> None:
        self.pending.append(instance)

    def _autoflush(self) -> None:
        self.stored.extend(row for row in self.pending if isinstance(row, ProposalApproval))
        self.pending.clear()

    async def scalar(self, _statement: object) -> Proposal:
        self._autoflush()
        return self.proposal

    async def scalars(self, _statement: object) -> list[UUID]:
        self._autoflush()
        return [row.approver_id for row in self.stored if row.decision == "approved"]


def make_proposal(tenant_id: UUID, author_id: UUID, required_approver_count: int) -> Proposal:
    return Proposal(
        id=uuid4(),
        tenant_id=tenant_id,
        site_id=uuid4(),
        author_id=author_id,
        risk="medium",
        status="review_required",
        policy_evaluation_json={"required_approver_count": required_approver_count},
        expires_at=datetime.now(UTC) + timedelta(days=7),
        version=1,
    )


def service(session: AutoflushingSession, context: TenantContext) -> ProposalService:
    """The fake stands in for a session; only the members the service touches exist."""
    return ProposalService(cast(AsyncSession, session), context)


def approver(tenant_id: UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, actor_id=uuid4(), role=Role.SEO_MANAGER, trace_id="tr-approval"
    )


def stored_approval(tenant_id: UUID, proposal: Proposal, approver_id: UUID) -> ProposalApproval:
    return ProposalApproval(
        tenant_id=tenant_id,
        proposal_id=proposal.id,
        proposal_version=proposal.version,
        approver_id=approver_id,
        decision="approved",
    )


@pytest.mark.asyncio
async def test_one_approver_cannot_satisfy_a_two_approver_requirement() -> None:
    tenant_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=uuid4(), required_approver_count=2)
    session = AutoflushingSession(proposal)
    context = approver(tenant_id)

    await service(session, context).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="approved", notes="first")
    )

    assert proposal.status == "review_required"


@pytest.mark.asyncio
async def test_a_second_distinct_approver_completes_the_two_person_rule() -> None:
    tenant_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=uuid4(), required_approver_count=2)
    first = uuid4()
    session = AutoflushingSession(proposal, [stored_approval(tenant_id, proposal, first)])
    context = approver(tenant_id)

    await service(session, context).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="approved", notes="second")
    )

    assert proposal.status == "approved"


@pytest.mark.asyncio
async def test_the_same_approver_twice_does_not_complete_the_two_person_rule() -> None:
    """Defence in depth behind the unique constraint on (proposal, version, approver)."""
    tenant_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=uuid4(), required_approver_count=2)
    context = approver(tenant_id)
    session = AutoflushingSession(
        proposal, [stored_approval(tenant_id, proposal, context.actor_id)]
    )

    await service(session, context).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="approved", notes="again")
    )

    assert proposal.status == "review_required"


@pytest.mark.asyncio
async def test_a_single_approver_still_completes_a_low_risk_proposal() -> None:
    tenant_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=uuid4(), required_approver_count=1)
    session = AutoflushingSession(proposal)

    await service(session, approver(tenant_id)).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="approved", notes="only")
    )

    assert proposal.status == "approved"


@pytest.mark.asyncio
async def test_a_policy_record_missing_the_count_fails_closed_to_two_approvers() -> None:
    tenant_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=uuid4(), required_approver_count=1)
    proposal.policy_evaluation_json = {}
    session = AutoflushingSession(proposal)

    await service(session, approver(tenant_id)).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="approved", notes="only")
    )

    assert proposal.status == "review_required"


@pytest.mark.asyncio
async def test_rejection_still_rejects_regardless_of_approver_count() -> None:
    tenant_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=uuid4(), required_approver_count=2)
    session = AutoflushingSession(proposal)

    await service(session, approver(tenant_id)).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="rejected", notes="no")
    )

    assert proposal.status == "rejected"


@pytest.mark.asyncio
async def test_author_is_still_refused_before_any_counting_happens() -> None:
    tenant_id = uuid4()
    author_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=2)
    session = AutoflushingSession(proposal)
    context = TenantContext(
        tenant_id=tenant_id, actor_id=author_id, role=Role.SEO_MANAGER, trace_id="tr-approval"
    )

    with pytest.raises(HTTPException) as exc:
        await service(session, context).approve_proposal(
            proposal.id, ProposalApprovalCreate(decision="approved", notes="mine")
        )

    assert exc.value.detail == "author_cannot_approve_own_proposal"
    assert proposal.status == "review_required"
