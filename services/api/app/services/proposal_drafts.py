"""Turn a ranked opportunity into a reviewable proposal.

This is the step the change loop was missing. Findings, scoring and ranking
already ran; `ProposalService.create_proposal` already gates what a proposal may
become. Between them, nothing built the diff, so every proposal had to be
hand-authored — which is why the loop had never run end to end.

A draft is not a proposal. Nothing here writes a row: it returns a
`ProposalCreate` for the caller to submit through the ordinary gate, so risk
classification, validators, policy and approval are unchanged and unbypassed.

Only rules with a deterministic repair are handled, and each one refuses far
more often than it acts. An opportunity this cannot answer is left for a human
rather than answered badly.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ProposalCreate
from app.core.context import TenantContext
from app.db.models import (
    Finding,
    Opportunity,
    OpportunityFinding,
    Page,
    PageObservation,
    Proposal,
)
from app.domain.h1_repair import H1Repair, H1RepairError, is_site_root, plan_repair
from app.domain.title_repair import TitleRepair
from app.domain.title_repair import repair_document as repair_title

# Reading a file is the connector's job, not this service's. Passing it in keeps
# the draft logic testable without a network and without a token.
ReadFile = Callable[[str], Awaitable[str | None]]

# Both rules describe the same broken heading from different angles, and both
# are repaired the same way.
HEADING_RULES = frozenset({"content.title_h1_mismatch", "h1.duplicate_across_site"})
# A different repair with a different shape: the title itself is what is wrong,
# so deriving a heading from it would only propagate the omission.
TITLE_RULES = frozenset({"content.title_omits_url_topic"})
REPAIRABLE_RULES = HEADING_RULES | TITLE_RULES

# A proposal in any of these is still on its way somewhere, so a second one for
# the same opportunity would be a duplicate rather than a replacement.
LIVE_PROPOSAL_STATUSES = ("draft", "validated", "review_required", "approved", "deployed")

RATIONALE = (
    "The heading is taken from this page's own <title>, so it names the page's "
    "subject instead of repeating a heading the rest of the site also uses. "
    "Nothing else in the file changes."
)

TITLE_RATIONALE = (
    "The URL already says what this page is; the title and heading did not. "
    "The subject word is taken from the page's own slug, so nothing is invented, "
    "and the site's title suffix is kept verbatim. Nothing else in the file "
    "changes."
)


class ProposalDraftService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        path_template: str = "{path}.html",
    ) -> None:
        self.session = session
        self.context = context
        self.path_template = path_template

    async def draft_from_opportunity(
        self, opportunity_id: UUID, read_file: ReadFile
    ) -> tuple[UUID, ProposalCreate]:
        """Return the site the change belongs to, and the change itself."""
        opportunity = await self._load_opportunity(opportunity_id)
        await self._refuse_if_already_proposed(opportunity_id)
        page = await self._load_page(opportunity.page_id)
        rule_key = await self._repairable_rule(opportunity_id)

        if is_site_root(page.normalized_url):
            # The rules are right that the front page shares the heading; both
            # repairs are wrong there, so refuse before reading any file.
            raise self._refuse("h1_repair_refuses_site_root")

        observation = await self._latest_observation(page.id)
        if not observation.title:
            raise self._refuse("draft_requires_a_page_title")

        target_path = self._target_path(page.normalized_url)
        document = await read_file(target_path)
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"draft_target_not_found:{target_path}",
            )

        if rule_key in TITLE_RULES:
            return opportunity.site_id, self._title_proposal(
                opportunity_id, page, rule_key, target_path, document
            )

        repair = self._repair(document, observation.title, page.normalized_url)
        return opportunity.site_id, ProposalCreate(
            opportunity_id=opportunity_id,
            page_id=page.id,
            title=f"Set the H1 on {self._display_path(page.normalized_url)} to “{repair.heading}”",
            rationale=(
                f"{RATIONALE} Rule: {rule_key}. "
                f"Replaces “{repair.previous_heading}” with “{repair.heading}”."
            ),
            target_type="github_file",
            target_path=target_path,
            before_content=repair.before_content,
            after_content=repair.after_content,
        )

    def _title_proposal(
        self,
        opportunity_id: UUID,
        page: Any,
        rule_key: str,
        target_path: str,
        document: str,
    ) -> ProposalCreate:
        """One change covering the title and the heading, or a named refusal."""
        repair: TitleRepair = repair_title(document, page.normalized_url)
        if not repair.applied:
            # The reason is the useful part: it names which page shape defeated
            # the repair, so a person knows what to look at rather than being
            # told only that nothing happened.
            raise self._refuse(repair.reason)
        return ProposalCreate(
            opportunity_id=opportunity_id,
            page_id=page.id,
            title=(
                f"Name the subject on {self._display_path(page.normalized_url)}: "
                f"“{repair.heading_after}”"
            ),
            rationale=(
                f"{TITLE_RATIONALE} Rule: {rule_key}. "
                f"Title “{repair.title_before}” becomes “{repair.title_after}”; "
                f"H1 “{repair.heading_before}” becomes “{repair.heading_after}”."
            ),
            target_type="github_file",
            target_path=target_path,
            before_content=repair.before,
            after_content=repair.after,
        )

    async def _refuse_if_already_proposed(self, opportunity_id: UUID) -> None:
        """One opportunity, one live proposal.

        Drafting the same opportunity twice produces two proposals for one page.
        Deployed together they write the same file twice in one branch, where
        the second silently wins and the first receipt claims an effect that
        never happened; deployed apart they are two pull requests undoing each
        other. A proposal that is finished — deployed, rejected or expired —
        does not block a new one, because the opportunity being open again
        means the page still needs the change.
        """
        live = await self.session.scalar(
            select(Proposal.id).where(
                Proposal.tenant_id == self.context.tenant_id,
                Proposal.opportunity_id == opportunity_id,
                Proposal.status.in_(LIVE_PROPOSAL_STATUSES),
            )
        )
        if live is not None:
            raise self._refuse(f"opportunity_already_has_a_live_proposal:{live}")

    def _repair(self, document: str, title: str, normalized_url: str) -> H1Repair:
        try:
            return plan_repair(document, title, normalized_url)
        except H1RepairError as error:
            # The refusal reason is the useful part: it says which page shape
            # defeated the repair, so a human knows what to look at.
            raise self._refuse(str(error)) from error

    def _target_path(self, normalized_url: str) -> str:
        path = urlsplit(normalized_url).path.strip("/") or "index"
        return self.path_template.format(path=path)

    def _display_path(self, normalized_url: str) -> str:
        return urlsplit(normalized_url).path or "/"

    async def _load_opportunity(self, opportunity_id: UUID) -> Opportunity:
        opportunity = await self.session.scalar(
            select(Opportunity).where(
                Opportunity.id == opportunity_id,
                Opportunity.tenant_id == self.context.tenant_id,
            )
        )
        if opportunity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="opportunity_not_found"
            )
        if opportunity.status != "open":
            # A suppressed opportunity was judged invalid, most often because
            # its evidence was. Drafting from it would reintroduce the finding.
            raise self._refuse(f"opportunity_not_open:{opportunity.status}")
        return opportunity

    async def _load_page(self, page_id: UUID) -> Page:
        page = await self.session.scalar(
            select(Page).where(Page.id == page_id, Page.tenant_id == self.context.tenant_id)
        )
        if page is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page_not_found")
        return page

    async def _repairable_rule(self, opportunity_id: UUID) -> str:
        rule_keys = set(
            (
                await self.session.scalars(
                    select(Finding.rule_key)
                    .join(OpportunityFinding, OpportunityFinding.finding_id == Finding.id)
                    .where(
                        OpportunityFinding.opportunity_id == opportunity_id,
                        OpportunityFinding.tenant_id == self.context.tenant_id,
                        Finding.tenant_id == self.context.tenant_id,
                    )
                )
            ).all()
        )
        repairable = sorted(rule_keys & REPAIRABLE_RULES)
        if not repairable:
            raise self._refuse("opportunity_has_no_deterministic_repair")
        return repairable[0]

    async def _latest_observation(self, page_id: UUID) -> PageObservation:
        observation = await self.session.scalar(
            select(PageObservation)
            .where(
                PageObservation.page_id == page_id,
                PageObservation.tenant_id == self.context.tenant_id,
            )
            .order_by(PageObservation.observed_at.desc())
            .limit(1)
        )
        if observation is None:
            raise self._refuse("draft_requires_crawl_evidence")
        return observation

    @staticmethod
    def _refuse(detail: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
