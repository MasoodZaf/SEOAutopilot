"""AI-written copy, from request to a person's edited copy.

Two kinds share this lifecycle: a new blog post written from a brief, and an
answer block -- short direct answers for an existing page's question headings,
which become an edit to that page's source file instead of a new one.

The lifecycle, and who moves it:

    queued -> running -> ready         the worker (a model writes it)
    ready -> ready                     a person edits, resolves flags
    ready -> submitted                 a person submits it as a proposal
    queued|ready|failed -> withdrawn   a person drops it

Nothing here reaches a site. A draft is input to a proposal, and a proposal
still needs its approvals and a merged pull request. What this layer adds is
the review a model's output needs before it is even eligible for that: every
factual claim the model made is listed as a flag, and a flag must be resolved
by a person before the draft can be submitted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    Connector,
    ContentBrief,
    ContentDraft,
    OutboxEvent,
    Page,
    PageObservation,
    Proposal,
    Site,
)
from app.domain.answer_block import AnswerBlockError, AnswerItem, place_answers
from app.services.proposal_drafts import target_path_for
from app.services.proposals import ProposalService
from app.services.tenant_credentials import ANTHROPIC_API_KEY, OPENAI_API_KEY, store_for

REQUEST_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR}
LIVE_STATUSES = {"queued", "running", "ready"}
WITHDRAWABLE = {"queued", "ready", "failed"}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# Kept in step with the worker's geo.question_without_direct_answer rule
# (services/worker/app/analysis_rules.py): the questions a draft answers are
# exactly the ones that rule reports.
DIRECT_ANSWER_MIN_WORDS = 15
DIRECT_ANSWER_MAX_WORDS = 100
MAX_ANSWER_ITEMS = 5
MAX_ANSWER_CHARS = 600

# Reading a file is the GitHub connector's job; the route passes it in.
ReadFile = Callable[[str], Awaitable[str | None]]


def unanswered_questions(outline: dict[str, Any] | None) -> list[str]:
    """Question headings with no quotable answer under them, in page order."""
    headings = (outline or {}).get("headings")
    if not isinstance(headings, list):
        return []
    found: list[str] = []
    for item in headings:
        if not isinstance(item, dict) or item.get("question") is not True:
            continue
        kind, words = item.get("answer_kind"), item.get("answer_words")
        if kind in ("list", "table"):
            continue
        if kind != "none" and isinstance(words, int) and DIRECT_ANSWER_MIN_WORDS <= words <= DIRECT_ANSWER_MAX_WORDS:
            continue
        text = " ".join(str(item.get("text") or "").split())
        if text and text not in found:
            found.append(text)
    return found[:MAX_ANSWER_ITEMS]


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class ContentDraftService:
    def __init__(self, session: AsyncSession, context: TenantContext, settings: Settings) -> None:
        self.session = session
        self.context = context
        self.settings = settings

    def _require_editor(self) -> None:
        if self.context.role not in REQUEST_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_content_draft",
            )

    async def get(self, draft_id: UUID) -> ContentDraft | None:
        return await self.session.scalar(
            select(ContentDraft).where(
                ContentDraft.id == draft_id,
                ContentDraft.tenant_id == self.context.tenant_id,
            )
        )

    async def list_for_site(self, site_id: UUID, limit: int) -> list[ContentDraft]:
        rows = await self.session.scalars(
            select(ContentDraft)
            .where(
                ContentDraft.tenant_id == self.context.tenant_id,
                ContentDraft.site_id == site_id,
            )
            .order_by(ContentDraft.created_at.desc(), ContentDraft.id)
            .limit(min(max(limit, 1), 100))
        )
        return list(rows)

    async def spent_this_month(self) -> int:
        spent = await self.session.scalar(
            select(func.coalesce(func.sum(ContentDraft.cost_micros), 0)).where(
                ContentDraft.tenant_id == self.context.tenant_id,
                ContentDraft.created_at >= month_start(datetime.now(UTC)),
            )
        )
        return int(spent or 0)

    async def _has_key(self, provider: str) -> bool:
        """Whether the workspace stored its own key for this provider.

        Bring-your-own-key only: there is deliberately no deployment fallback.
        """
        credential = {"anthropic": ANTHROPIC_API_KEY, "openai": OPENAI_API_KEY}.get(provider)
        if credential is None:
            return False
        try:
            store = store_for(self.session, self.settings)
        except HTTPException:
            return False
        return await store.describe(self.context.tenant_id, credential) is not None

    async def request(
        self, brief_id: UUID, idempotency_key: str, author_name: str, provider: str = "anthropic"
    ) -> ContentDraft:
        """Queue a draft for a new-post brief.

        Refused rather than queued when it could only fail later: a brief that
        is a page refresh, no key to pay with, or a month's budget already
        spent. The budget is checked here and again by the worker, which is
        the check that holds under concurrency.
        """
        self._require_editor()
        brief = await self.session.scalar(
            select(ContentBrief).where(
                ContentBrief.id == brief_id,
                ContentBrief.tenant_id == self.context.tenant_id,
            )
        )
        if brief is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="content_brief_not_found")
        if brief.kind != "new_page":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_brief_not_new_post"
            )
        if brief.status == "dismissed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_brief_dismissed"
            )
        existing = await self.session.scalar(
            select(ContentDraft).where(
                ContentDraft.tenant_id == self.context.tenant_id,
                ContentDraft.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        if not await self._has_key(provider):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"{provider}_key_not_configured"
            )
        if await self.spent_this_month() >= self.settings.content_draft_monthly_budget_micros:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_budget_exhausted"
            )

        draft = ContentDraft(
            tenant_id=self.context.tenant_id,
            site_id=brief.site_id,
            content_brief_id=brief.id,
            requested_by=self.context.actor_id,
            status="queued",
            idempotency_key=idempotency_key,
            provider=provider,
            request_hash=_hash(
                {"brief_id": str(brief.id), "brief_version": brief.version, "provider": provider}
            ),
            author_name=author_name.strip()[:120] or None,
            flags_json=[],
        )
        try:
            async with self.session.begin_nested():
                self.session.add(draft)
                await self.session.flush()
        except IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_already_live"
            ) from error

        payload = {"content_draft_id": str(draft.id), "site_id": str(brief.site_id)}
        self.session.add_all(
            [
                self._audit_event("content_draft.requested", draft.id, {"content_brief_id": str(brief.id)}),
                OutboxEvent(
                    tenant_id=self.context.tenant_id,
                    event_type="content_draft.requested.v1",
                    event_version=1,
                    aggregate_type="content_draft",
                    aggregate_id=draft.id,
                    payload=payload,
                ),
            ]
        )
        return draft

    async def request_answers(
        self, page_id: UUID, idempotency_key: str, provider: str = "anthropic"
    ) -> ContentDraft:
        """Queue direct answers for a page's unanswered question headings.

        The questions are read here from the page's latest crawl, never taken
        from the request. Refused when there is nothing to answer, no key to
        pay with, or the month's budget is spent.
        """
        self._require_editor()
        page = await self.session.scalar(
            select(Page).where(Page.id == page_id, Page.tenant_id == self.context.tenant_id)
        )
        if page is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page_not_found")
        existing = await self.session.scalar(
            select(ContentDraft).where(
                ContentDraft.tenant_id == self.context.tenant_id,
                ContentDraft.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        outline = await self.session.scalar(
            select(PageObservation.answer_outline_json)
            .where(
                PageObservation.tenant_id == self.context.tenant_id,
                PageObservation.page_id == page.id,
                PageObservation.answer_outline_json.is_not(None),
            )
            .order_by(PageObservation.observed_at.desc(), PageObservation.id.desc())
            .limit(1)
        )
        questions = unanswered_questions(outline)
        if not questions:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="page_has_no_unanswered_questions"
            )
        if not await self._has_key(provider):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"{provider}_key_not_configured"
            )
        if await self.spent_this_month() >= self.settings.content_draft_monthly_budget_micros:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_budget_exhausted"
            )

        draft = ContentDraft(
            tenant_id=self.context.tenant_id,
            site_id=page.site_id,
            kind="answer_block",
            page_id=page.id,
            requested_by=self.context.actor_id,
            status="queued",
            idempotency_key=idempotency_key,
            provider=provider,
            request_hash=_hash({"page_id": str(page.id), "questions": questions, "provider": provider}),
            answer_items_json=[{"heading": question, "answer": None} for question in questions],
            flags_json=[],
        )
        try:
            async with self.session.begin_nested():
                self.session.add(draft)
                await self.session.flush()
        except IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="answer_draft_already_live"
            ) from error

        payload = {"content_draft_id": str(draft.id), "site_id": str(page.site_id)}
        self.session.add_all(
            [
                self._audit_event(
                    "content_draft.requested",
                    draft.id,
                    {"kind": "answer_block", "page_id": str(page.id), "questions": len(questions)},
                ),
                OutboxEvent(
                    tenant_id=self.context.tenant_id,
                    event_type="content_draft.requested.v1",
                    event_version=1,
                    aggregate_type="content_draft",
                    aggregate_id=draft.id,
                    payload=payload,
                ),
            ]
        )
        return draft

    async def update(
        self,
        draft_id: UUID,
        *,
        expected_version: int,
        title: str | None,
        slug: str | None,
        meta_description: str | None,
        body_markdown: str | None,
        author_name: str | None,
        resolved_flags: dict[str, str],
        answers: dict[str, str] | None = None,
    ) -> ContentDraft:
        """Apply a person's edits to a ready draft.

        `expected_version` makes a stale form fail loudly instead of silently
        overwriting a colleague's edit. `resolved_flags` maps a flag id to the
        reviewer's note on how it was resolved (verified, corrected, removed).
        """
        self._require_editor()
        draft = await self._editable(draft_id)
        if draft.version != expected_version:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="content_draft_stale")
        if slug is not None:
            slug = slug.strip().lower()
            if not SLUG_PATTERN.fullmatch(slug) or len(slug) > 80:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="content_draft_slug_invalid",
                )
            draft.slug = slug
        if title is not None:
            draft.title = title.strip()[:200]
        if meta_description is not None:
            draft.meta_description = meta_description.strip()[:320]
        if body_markdown is not None:
            draft.body_markdown = body_markdown[:60000]
        if author_name is not None:
            draft.author_name = author_name.strip()[:120] or None
        if answers is not None:
            if draft.kind != "answer_block":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="content_draft_has_no_answers",
                )
            items = [dict(item) for item in draft.answer_items_json or []]
            known = {str(item.get("heading")) for item in items}
            if set(answers) - known:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="answer_heading_unknown",
                )
            for item in items:
                text = answers.get(str(item.get("heading")))
                if text is not None:
                    item["answer"] = " ".join(text.split())[:MAX_ANSWER_CHARS] or None
            draft.answer_items_json = items

        flags: list[dict[str, Any]] = []
        for flag in draft.flags_json or []:
            item = dict(flag)
            note = resolved_flags.get(str(item.get("id")))
            if note is not None:
                item["resolved"] = True
                item["resolution"] = note.strip()[:300] or "resolved"
                item["resolved_by"] = str(self.context.actor_id)
            flags.append(item)
        draft.flags_json = flags
        draft.version += 1
        draft.updated_at = datetime.now(UTC)
        self.session.add(
            self._audit_event(
                "content_draft.edited",
                draft.id,
                {"version": draft.version, "resolved": sorted(resolved_flags)},
            )
        )
        await self.session.flush()
        return draft

    async def submit(
        self,
        draft_id: UUID,
        read_file: ReadFile | None = None,
        path_template: str | None = None,
    ) -> Proposal:
        """Send a reviewed draft for approval, as a proposal to add the post.

        Refused until the review is done: every flag resolved, and a title,
        slug, body and named author present. The proposal then goes through
        the same gates as any other change -- high risk, so at least two
        approvers who are not its author -- and deploying it opens a pull
        request that a person merges.
        """
        self._require_editor()
        draft = await self._editable(draft_id)
        if unresolved_flags(draft):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_flags_unresolved"
            )
        if draft.kind == "answer_block":
            if read_file is None or path_template is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="github_not_connected"
                )
            return await self._submit_answers(draft, read_file, path_template)
        if not (draft.title and draft.slug and draft.body_markdown and draft.author_name):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="content_draft_incomplete",
            )
        site = await self.session.scalar(
            select(Site).where(Site.id == draft.site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        connector = await self.session.scalar(
            select(Connector).where(
                Connector.tenant_id == self.context.tenant_id,
                Connector.site_id == site.id,
                Connector.type == "github_repository",
                Connector.status == "active",
            )
        )
        if connector is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="github_not_connected"
            )
        template = str(
            (connector.config_json or {}).get("blog_path_template")
            or self.settings.github_blog_path_template
        )
        target_path = blog_target_path(template, draft.slug)

        # The page the post will become, named the way the crawler names it
        # (origin + path, no trailing slash) so its first crawl finds this row
        # instead of creating a second. `planned` until that crawl sees it.
        path = "/" + self.settings.blog_url_template.replace("{slug}", draft.slug).strip("/")
        url = site.canonical_origin.rstrip("/") + path
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        page = await self.session.scalar(
            select(Page).where(Page.site_id == site.id, Page.url_hash == url_hash)
        )
        if page is None:
            page = Page(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                normalized_url=url,
                url_hash=url_hash,
                lifecycle_status="planned",
            )
            self.session.add(page)
            await self.session.flush()

        flags = draft.flags_json or []
        rationale = (
            f"New post from a reviewed AI draft ({draft.provider}, {draft.model or 'model'}). "
            f"{len(flags)} review flag(s) resolved by a person. Publishes at {path}."
        )
        try:
            async with self.session.begin_nested():
                proposal = await ProposalService(self.session, self.context).create_new_page(
                    site,
                    page_id=page.id,
                    content_draft_id=draft.id,
                    title=f"New post: {draft.title}"[:240],
                    rationale=rationale,
                    target_path=target_path,
                    after_content=render_post(draft, datetime.now(UTC).date().isoformat()),
                    evidence_refs={
                        "content_draft_id": str(draft.id),
                        "content_brief_id": str(draft.content_brief_id),
                        "flags_resolved": len(flags),
                        "provider": draft.provider,
                        "model": draft.model,
                    },
                )
        except IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_already_submitted"
            ) from error

        draft.status = "submitted"
        draft.version += 1
        draft.updated_at = datetime.now(UTC)
        self.session.add(
            self._audit_event(
                "content_draft.submitted", draft.id, {"proposal_id": str(proposal.id), "target_path": target_path}
            )
        )
        await self.session.flush()
        return proposal

    async def _submit_answers(
        self, draft: ContentDraft, read_file: ReadFile, path_template: str
    ) -> Proposal:
        """Propose the reviewed answers as an edit to the page's own file."""
        items = [
            AnswerItem(str(item.get("heading") or ""), str(item.get("answer") or ""))
            for item in draft.answer_items_json or []
            if str(item.get("answer") or "").strip()
        ]
        if not items:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="answer_draft_empty"
            )
        site = await self.session.scalar(
            select(Site).where(Site.id == draft.site_id, Site.tenant_id == self.context.tenant_id)
        )
        page = await self.session.scalar(
            select(Page).where(Page.id == draft.page_id, Page.tenant_id == self.context.tenant_id)
        )
        if site is None or page is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page_not_found")
        target_path = target_path_for(path_template, page.normalized_url)
        document = await read_file(target_path)
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"draft_target_not_found:{target_path}",
            )
        try:
            edit = place_answers(document, target_path, items)
        except AnswerBlockError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        flags = draft.flags_json or []
        rationale = (
            f"Direct answers for {edit.placed} question heading(s), from a reviewed AI draft "
            f"({draft.provider}, {draft.model or 'model'}), with FAQPage markup built from them. "
            f"{len(flags)} review flag(s) resolved by a person. Rule: geo.question_without_direct_answer."
        )
        try:
            async with self.session.begin_nested():
                proposal = await ProposalService(self.session, self.context).create_page_edit(
                    site,
                    page_id=page.id,
                    content_draft_id=draft.id,
                    title=f"Answer {edit.placed} question(s) on {page.normalized_url}"[:240],
                    rationale=rationale,
                    target_path=target_path,
                    before_content=edit.before,
                    after_content=edit.after,
                    evidence_refs={
                        "content_draft_id": str(draft.id),
                        "kind": "answer_block",
                        "questions": [item.heading for item in items],
                        "flags_resolved": len(flags),
                        "provider": draft.provider,
                        "model": draft.model,
                    },
                )
        except IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_already_submitted"
            ) from error

        draft.status = "submitted"
        draft.version += 1
        draft.updated_at = datetime.now(UTC)
        self.session.add(
            self._audit_event(
                "content_draft.submitted",
                draft.id,
                {"proposal_id": str(proposal.id), "target_path": target_path, "kind": "answer_block"},
            )
        )
        await self.session.flush()
        return proposal

    async def withdraw(self, draft_id: UUID) -> ContentDraft:
        self._require_editor()
        draft = await self.get(draft_id)
        if draft is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="content_draft_not_found")
        if draft.status not in WITHDRAWABLE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_not_withdrawable"
            )
        draft.status = "withdrawn"
        draft.version += 1
        draft.updated_at = datetime.now(UTC)
        self.session.add(self._audit_event("content_draft.withdrawn", draft.id, {}))
        await self.session.flush()
        return draft

    async def _editable(self, draft_id: UUID) -> ContentDraft:
        draft = await self.get(draft_id)
        if draft is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="content_draft_not_found")
        if draft.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="content_draft_not_editable"
            )
        return draft

    def _audit_event(self, action: str, draft_id: UUID, metadata: dict[str, Any]) -> AuditEvent:
        body = {**metadata, "actor_id": str(self.context.actor_id), "content_draft_id": str(draft_id)}
        return AuditEvent(
            tenant_id=self.context.tenant_id,
            actor_type="user",
            actor_id=str(self.context.actor_id),
            action=action,
            resource_type="content_draft",
            resource_id=str(draft_id),
            trace_id=self.context.trace_id,
            metadata_json=metadata,
            event_hash=_hash(body),
        )


def _yaml(value: str) -> str:
    """A single-line YAML double-quoted scalar."""
    flat = " ".join(value.split())
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_post(draft: ContentDraft, published_on: str) -> str:
    """The file a static-site generator reads: front matter, then the body."""
    front = [
        "---",
        f"title: {_yaml(draft.title or '')}",
        f"description: {_yaml(draft.meta_description or '')}",
        f"author: {_yaml(draft.author_name or '')}",
        f"date: {published_on}",
        f"slug: {draft.slug}",
        "draft: false",
        "---",
        "",
    ]
    body = (draft.body_markdown or "").replace("\r\n", "\n").strip()
    return "\n".join(front) + body + "\n"


def blog_target_path(template: str, slug: str) -> str:
    """Where the post lands in the repository, refusing anything unsafe."""
    if "{slug}" not in template or template.startswith("/") or ".." in template or "\\" in template:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="blog_path_template_invalid"
        )
    return template.replace("{slug}", slug)


def unresolved_flags(draft: ContentDraft) -> list[dict[str, Any]]:
    return [flag for flag in draft.flags_json or [] if not flag.get("resolved")]
