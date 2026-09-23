"""AI-written blog posts, from request to a person's edited copy.

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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, ContentBrief, ContentDraft, OutboxEvent
from app.services.tenant_credentials import ANTHROPIC_API_KEY, OPENAI_API_KEY, store_for

REQUEST_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR}
LIVE_STATUSES = {"queued", "running", "ready"}
WITHDRAWABLE = {"queued", "ready", "failed"}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


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


def unresolved_flags(draft: ContentDraft) -> list[dict[str, Any]]:
    return [flag for flag in draft.flags_json or [] if not flag.get("resolved")]
