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


@pytest.mark.asyncio
async def test_an_author_may_withdraw_their_own_proposal() -> None:
    """Separation of duties guards the deploy, not the bin.

    Seventeen title repairs were drafted on the pilot site and one of them was
    wrong. Before this, the only way to clear it was to spend a second person's
    approval on a change nobody wanted -- so the wrong change sat in the queue.
    """
    tenant_id = uuid4()
    author_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=2)
    session = AutoflushingSession(proposal)
    context = TenantContext(
        tenant_id=tenant_id, actor_id=author_id, role=Role.SEO_MANAGER, trace_id="tr-approval"
    )

    await service(session, context).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="rejected", notes="stutters")
    )

    assert proposal.status == "rejected"


@pytest.mark.asyncio
async def test_a_withdrawal_cannot_be_walked_back_into_an_approval() -> None:
    """The whole safety of the above rests on rejection being terminal.

    If an author could withdraw and then approve, they would have written and
    cleared their own change alone -- exactly what the rule exists to stop.
    """
    tenant_id = uuid4()
    author_id = uuid4()
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=2)
    session = AutoflushingSession(proposal)
    context = TenantContext(
        tenant_id=tenant_id, actor_id=author_id, role=Role.SEO_MANAGER, trace_id="tr-approval"
    )

    await service(session, context).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="rejected", notes="withdrawn")
    )
    with pytest.raises(HTTPException) as exc:
        await service(session, context).approve_proposal(
            proposal.id, ProposalApprovalCreate(decision="approved", notes="mine")
        )

    assert exc.value.detail == "proposal_already_rejected"
    assert proposal.status == "rejected"


class MembershipAwareSession(AutoflushingSession):
    """Answers the approver-membership query separately from the approval count."""

    def __init__(self, proposal: Proposal, approver_members: list[UUID]) -> None:
        super().__init__(proposal)
        self.approver_members = approver_members

    async def scalars(self, statement: object) -> list[UUID]:
        if "tenant_membership" in str(statement):
            return list(self.approver_members)
        return await super().scalars(statement)


def owner_author(tenant_id: UUID, author_id: UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, actor_id=author_id, role=Role.OWNER, trace_id="tr-self-review"
    )


@pytest.mark.asyncio
async def test_the_only_approver_may_self_review_a_medium_risk_change() -> None:
    """A one-person workspace could find problems but never ship a fix."""
    tenant_id, author_id = uuid4(), uuid4()
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=2)
    session = MembershipAwareSession(proposal, [author_id])

    approval = await service(session, owner_author(tenant_id, author_id)).approve_proposal(
        proposal.id, ProposalApprovalCreate(decision="approved", self_review=True)
    )

    assert proposal.status == "approved"
    assert approval.self_reviewed is True


@pytest.mark.asyncio
async def test_self_review_needs_the_explicit_confirmation() -> None:
    tenant_id, author_id = uuid4(), uuid4()
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=1)
    session = MembershipAwareSession(proposal, [author_id])

    with pytest.raises(HTTPException) as exc:
        await service(session, owner_author(tenant_id, author_id)).approve_proposal(
            proposal.id, ProposalApprovalCreate(decision="approved")
        )

    assert exc.value.detail == "self_review_confirmation_required"
    assert proposal.status == "review_required"


@pytest.mark.asyncio
async def test_high_risk_still_needs_a_second_person_in_a_solo_workspace() -> None:
    tenant_id, author_id = uuid4(), uuid4()
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=2)
    proposal.risk = "high"
    session = MembershipAwareSession(proposal, [author_id])

    with pytest.raises(HTTPException) as exc:
        await service(session, owner_author(tenant_id, author_id)).approve_proposal(
            proposal.id, ProposalApprovalCreate(decision="approved", self_review=True)
        )

    assert exc.value.detail == "self_review_not_allowed_for_risk"
    assert proposal.status == "review_required"


@pytest.mark.asyncio
async def test_self_review_ends_once_a_second_approver_exists() -> None:
    tenant_id, author_id = uuid4(), uuid4()
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=1)
    session = MembershipAwareSession(proposal, [author_id, uuid4()])

    with pytest.raises(HTTPException) as exc:
        await service(session, owner_author(tenant_id, author_id)).approve_proposal(
            proposal.id, ProposalApprovalCreate(decision="approved", self_review=True)
        )

    assert exc.value.detail == "author_cannot_approve_own_proposal"


def new_blog_post(tenant_id: UUID, author_id: UUID, body: str = "---\ntitle: EMI\n---\nHow EMI works.\n") -> Proposal:
    proposal = make_proposal(tenant_id, author_id=author_id, required_approver_count=2)
    proposal.risk = "high"
    proposal.target_type = "github_file"
    proposal.target_path = "content/blog/emi.md"
    proposal.before_content = ""
    proposal.after_content = body
    proposal.content_draft_id = uuid4()
    return proposal


@pytest.mark.asyncio
async def test_the_only_approver_may_approve_a_new_blog_post_after_both_confirmations() -> None:
    tenant_id, author_id = uuid4(), uuid4()
    proposal = new_blog_post(tenant_id, author_id)
    session = MembershipAwareSession(proposal, [author_id])

    with pytest.raises(HTTPException) as exc:
        await service(session, owner_author(tenant_id, author_id)).approve_proposal(
            proposal.id, ProposalApprovalCreate(decision="approved", self_review=True)
        )
    assert exc.value.detail == "self_review_post_confirmation_required"

    approval = await service(session, owner_author(tenant_id, author_id)).approve_proposal(
        proposal.id,
        ProposalApprovalCreate(decision="approved", self_review=True, self_review_post=True),
    )
    assert proposal.status == "approved"
    assert approval.self_reviewed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"content_draft_id": None},  # a new file that is not a reviewed blog draft
        {"before_content": "<p>old</p>"},  # an edit to an existing page
        {"target_path": "public/robots.txt"},
        {"after_content": '<meta name="robots" content="noindex">'},
    ],
)
async def test_other_high_risk_changes_still_need_a_second_person(change: dict[str, object]) -> None:
    tenant_id, author_id = uuid4(), uuid4()
    proposal = new_blog_post(tenant_id, author_id)
    for key, value in change.items():
        setattr(proposal, key, value)
    session = MembershipAwareSession(proposal, [author_id])

    with pytest.raises(HTTPException) as exc:
        await service(session, owner_author(tenant_id, author_id)).approve_proposal(
            proposal.id,
            ProposalApprovalCreate(decision="approved", self_review=True, self_review_post=True),
        )
    assert exc.value.detail == "self_review_not_allowed_for_risk"
