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
    KeywordClusterMember,
    SearchQuery,
    Site,
)
from app.services.keywords import READ_TERM_ROLES, decrypt_term

# Matches the worker's per-run cap: a question beyond it would never be asked.
MAX_TRACKED = 8
MAX_SUGGESTIONS = 10
# Questions people typed come first, but never crowd out every topic suggestion.
MAX_SEARCH_SUGGESTIONS = 6
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
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        encryption_key: bytes | None = None,
    ) -> None:
        self.session = session
        self.context = context
        # Opens the search terms the worker sealed; without it, or for a role
        # that may not read terms, suggestions come from cluster labels only.
        self.encryption_key = encryption_key

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

    def _latest_keyword_run(self, site_id: UUID) -> Any:
        return (
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

    async def _suggestions(
        self, site_id: UUID, tracked: list[AiCitationPrompt]
    ) -> list[dict[str, Any]]:
        latest = self._latest_keyword_run(site_id)
        taken_text = {item.prompt.lower() for item in tracked}
        taken_cluster = {item.keyword_cluster_id for item in tracked if item.keyword_cluster_id}
        out = await self._search_console_suggestions(site_id, latest, taken_text)
        taken_text |= {item["prompt"].lower() for item in out}

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
        for cluster_id, label in clusters.all():
            if len(out) >= MAX_SUGGESTIONS:
                break
            question = as_question(str(label))
            if len(question) < 8 or cluster_id in taken_cluster or question.lower() in taken_text:
                continue
            out.append({
                "prompt": question[:300],
                "source": "question_cluster",
                "keyword_cluster_id": cluster_id,
                "query_hash": None,
                "impressions": None,
            })
        return out

    async def _search_console_suggestions(
        self, site_id: UUID, latest: Any, taken_text: set[str]
    ) -> list[dict[str, Any]]:
        """Questions people already typed into Google and saw this site for.

        The terms are sealed at rest. Opening them is limited to the roles that
        may read search terms, and each listing that opens any is audited, the
        same as reading a keyword cluster's members.
        """
        if self.context.role not in READ_TERM_ROLES or self.encryption_key is None or len(self.encryption_key) != 32:
            return []
        rows = await self.session.execute(
            select(SearchQuery, func.sum(KeywordClusterMember.impressions).label("impressions"))
            .join(
                KeywordClusterMember,
                (KeywordClusterMember.tenant_id == SearchQuery.tenant_id)
                & (KeywordClusterMember.site_id == SearchQuery.site_id)
                & (KeywordClusterMember.query_hash == SearchQuery.query_hash),
            )
            .join(
                KeywordCluster,
                (KeywordCluster.id == KeywordClusterMember.cluster_id)
                & (KeywordCluster.tenant_id == KeywordClusterMember.tenant_id),
            )
            .where(
                SearchQuery.tenant_id == self.context.tenant_id,
                SearchQuery.site_id == site_id,
                SearchQuery.is_question.is_(True),
                KeywordCluster.analysis_run_id == latest,
            )
            # Grouped by the primary key, so the row's other columns may be selected.
            .group_by(SearchQuery.tenant_id, SearchQuery.site_id, SearchQuery.query_hash)
            .order_by(func.sum(KeywordClusterMember.impressions).desc(), SearchQuery.query_hash)
            .limit(MAX_SEARCH_SUGGESTIONS * 3)
        )
        out: list[dict[str, Any]] = []
        unreadable = 0
        for stored, impressions in rows.all():
            term = decrypt_term(self.encryption_key, stored)
            if term is None:
                unreadable += 1
                continue
            question = as_question(term)
            if not 8 <= len(question) <= 300 or question.lower() in taken_text:
                continue
            taken_text.add(question.lower())
            out.append({
                "prompt": question,
                "source": "search_console",
                "keyword_cluster_id": None,
                "query_hash": stored.query_hash,
                "impressions": int(impressions or 0),
            })
            if len(out) >= MAX_SEARCH_SUGGESTIONS:
                break
        if out or unreadable:
            metadata = {
                "site_id": str(site_id),
                "purpose": "ai_citation_suggestions",
                "revealed_terms": len(out),
                "unreadable_terms": unreadable,
            }
            self.session.add(
                AuditEvent(
                    tenant_id=self.context.tenant_id,
                    actor_type="user",
                    actor_id=str(self.context.actor_id),
                    action="keyword_terms.read",
                    resource_type="site",
                    resource_id=str(site_id),
                    trace_id=self.context.trace_id,
                    metadata_json=metadata,
                    event_hash=_hash({**metadata, "actor_id": str(self.context.actor_id)}),
                )
            )
            await self.session.flush()
        return out

    async def track(
        self,
        site_id: UUID,
        prompt: str,
        keyword_cluster_id: UUID | None,
        query_hash: str | None = None,
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
        from_search = False
        if query_hash is not None:
            # Like the cluster id, a query hash is only provenance: it counts
            # when this site has that question on record, and is dropped if not.
            from_search = (
                await self.session.scalar(
                    select(SearchQuery.query_hash).where(
                        SearchQuery.tenant_id == self.context.tenant_id,
                        SearchQuery.site_id == site_id,
                        SearchQuery.query_hash == query_hash,
                        SearchQuery.is_question.is_(True),
                    )
                )
                is not None
            )
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
                source=(
                    "search_console"
                    if from_search
                    else "question_cluster" if keyword_cluster_id else "manual"
                ),
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
