"""Tracked questions and observed AI citations for a site.

A workspace chooses the questions; the worker's `ai_citation_scan` routine
asks them. Everything read here is scoped to the caller's tenant, and every
change to what is tracked is audited. Answer excerpts are third-party model
output and leave this service only as data for display.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import Role, TenantContext
from app.db.models import (
    AiCitationObservation,
    AiCitationPrompt,
    AiCitationRun,
    AuditEvent,
    KeywordAnalysisRun,
    KeywordCluster,
    Site,
)

# Matches the worker's per-run cap: a question beyond it would never be asked.
MAX_TRACKED = 8
MAX_SUGGESTIONS = 10
WRITE_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR}
QUESTION_WORDS = ("how", "what", "which", "why", "when", "where", "who", "is", "are", "can", "does", "do", "should")


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def as_question(label: str) -> str:
    """A cluster label read as the question it stands for."""
    text = " ".join(label.split())
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if text.split(" ", 1)[0].lower() in QUESTION_WORDS and not text.endswith("?"):
        text += "?"
    return text


class AiCitationService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def _site(self, site_id: UUID) -> Site:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        return site

    def _require_writer(self) -> None:
        if self.context.role not in WRITE_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_permissions"
            )

    async def prompts(self, site_id: UUID) -> tuple[list[AiCitationPrompt], list[dict[str, Any]]]:
        await self._site(site_id)
        tracked = list(
            await self.session.scalars(
                select(AiCitationPrompt)
                .where(
                    AiCitationPrompt.tenant_id == self.context.tenant_id,
                    AiCitationPrompt.site_id == site_id,
                    AiCitationPrompt.active.is_(True),
                )
                .order_by(AiCitationPrompt.created_at, AiCitationPrompt.id)
            )
        )
        return tracked, await self._suggestions(site_id, tracked)

    async def _suggestions(
        self, site_id: UUID, tracked: list[AiCitationPrompt]
    ) -> list[dict[str, Any]]:
        latest = (
            select(KeywordAnalysisRun.id)
            .where(
                KeywordAnalysisRun.tenant_id == self.context.tenant_id,
                KeywordAnalysisRun.site_id == site_id,
                KeywordAnalysisRun.status == "completed",
            )
            .order_by(KeywordAnalysisRun.created_at.desc(), KeywordAnalysisRun.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        clusters = await self.session.execute(
            select(KeywordCluster.id, KeywordCluster.label)
            .where(
                KeywordCluster.tenant_id == self.context.tenant_id,
                KeywordCluster.site_id == site_id,
                KeywordCluster.analysis_run_id == latest,
                KeywordCluster.answer_engine_candidate.is_(True),
            )
            .order_by(KeywordCluster.opportunity_score.desc(), KeywordCluster.id)
            .limit(MAX_SUGGESTIONS * 2)
        )
        taken_text = {item.prompt.lower() for item in tracked}
        taken_cluster = {item.keyword_cluster_id for item in tracked if item.keyword_cluster_id}
        out: list[dict[str, Any]] = []
        for cluster_id, label in clusters.all():
            question = as_question(str(label))
            if len(question) < 8 or cluster_id in taken_cluster or question.lower() in taken_text:
                continue
            out.append({"prompt": question[:300], "keyword_cluster_id": cluster_id})
            if len(out) >= MAX_SUGGESTIONS:
                break
        return out

    async def track(
        self, site_id: UUID, prompt: str, keyword_cluster_id: UUID | None
    ) -> AiCitationPrompt:
        self._require_writer()
        await self._site(site_id)
        text = " ".join(prompt.split())
        if not 8 <= len(text) <= 300:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="prompt_length_invalid"
            )
        if keyword_cluster_id is not None:
            # A cluster id from the client is only a label source; it must be
            # this site's, or it is dropped.
            owned = await self.session.scalar(
                select(KeywordCluster.id).where(
                    KeywordCluster.id == keyword_cluster_id,
                    KeywordCluster.tenant_id == self.context.tenant_id,
                    KeywordCluster.site_id == site_id,
                )
            )
            keyword_cluster_id = owned
        existing = await self.session.scalar(
            select(AiCitationPrompt).where(
                AiCitationPrompt.tenant_id == self.context.tenant_id,
                AiCitationPrompt.site_id == site_id,
                func.lower(AiCitationPrompt.prompt) == text.lower(),
            )
        )
        if existing is not None and existing.active:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="prompt_already_tracked")
        active = await self.session.scalar(
            select(func.count()).where(
                AiCitationPrompt.tenant_id == self.context.tenant_id,
                AiCitationPrompt.site_id == site_id,
                AiCitationPrompt.active.is_(True),
            )
        )
        if int(active or 0) >= MAX_TRACKED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="prompt_limit_reached")
        if existing is not None:
            # Re-tracking a retired question keeps its history continuous.
            existing.active = True
            prompt_row = existing
        else:
            prompt_row = AiCitationPrompt(
                tenant_id=self.context.tenant_id,
                site_id=site_id,
                prompt=text,
                source="question_cluster" if keyword_cluster_id else "manual",
                keyword_cluster_id=keyword_cluster_id,
                created_by=self.context.actor_id,
            )
            self.session.add(prompt_row)
        await self.session.flush()
        self.session.add(self._audit("ai_citation_prompt.tracked", prompt_row, site_id))
        await self.session.flush()
        return prompt_row

    async def untrack(self, site_id: UUID, prompt_id: UUID) -> None:
        self._require_writer()
        await self._site(site_id)
        prompt_row = await self.session.scalar(
            select(AiCitationPrompt).where(
                AiCitationPrompt.id == prompt_id,
                AiCitationPrompt.tenant_id == self.context.tenant_id,
                AiCitationPrompt.site_id == site_id,
            )
        )
        if prompt_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="prompt_not_found")
        # Retired, not deleted: past observations still name it.
        prompt_row.active = False
        self.session.add(self._audit("ai_citation_prompt.untracked", prompt_row, site_id))
        await self.session.flush()

    async def report(
        self, site_id: UUID, history: int = 12
    ) -> tuple[AiCitationRun | None, list[AiCitationObservation], list[AiCitationRun]]:
        await self._site(site_id)
        runs = list(
            await self.session.scalars(
                select(AiCitationRun)
                .where(
                    AiCitationRun.tenant_id == self.context.tenant_id,
                    AiCitationRun.site_id == site_id,
                )
                .order_by(AiCitationRun.started_at.desc(), AiCitationRun.id.desc())
                .limit(min(history, 52))
            )
        )
        latest = runs[0] if runs else None
        observations: list[AiCitationObservation] = []
        if latest is not None:
            observations = list(
                await self.session.scalars(
                    select(AiCitationObservation)
                    .where(
                        AiCitationObservation.tenant_id == self.context.tenant_id,
                        AiCitationObservation.run_id == latest.id,
                    )
                    .order_by(AiCitationObservation.prompt, AiCitationObservation.provider)
                )
            )
        return latest, observations, runs

    def _audit(self, action: str, prompt_row: AiCitationPrompt, site_id: UUID) -> AuditEvent:
        metadata = {"site_id": str(site_id), "prompt_length": len(prompt_row.prompt)}
        body = {**metadata, "action": action, "actor_id": str(self.context.actor_id), "prompt_id": str(prompt_row.id)}
        return AuditEvent(
            tenant_id=self.context.tenant_id,
            actor_type="user",
            actor_id=str(self.context.actor_id),
            action=action,
            resource_type="ai_citation_prompt",
            resource_id=str(prompt_row.id),
            trace_id=self.context.trace_id,
            metadata_json=metadata,
            event_hash=_hash(body),
        )
