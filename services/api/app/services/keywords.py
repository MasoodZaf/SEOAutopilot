"""Read model over the worker's keyword clusters.

Aggregate reads never touch the query envelope. The single-cluster member view
is the one path that decrypts a stored term, so it is role-gated and writes an
audit event naming the cluster it opened.
"""

import hashlib
import secrets as stdlib_secrets
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import KeywordMemberRead
from app.core.context import Role, TenantContext
from app.db.models import (
    AuditEvent,
    KeywordAnalysisRun,
    KeywordCluster,
    KeywordClusterMember,
    SearchQuery,
    Site,
)
from app.services.opportunities import stable_hash

# Viewers and developers see cluster shape and metrics; revealing the raw
# queries people typed is limited to the roles that act on them.
READ_TERM_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR}
MAX_CLUSTER_PAGE_SIZE = 100
MAX_MEMBER_PAGE_SIZE = 200


def query_aad(tenant_id: UUID, site_id: UUID, key_version: str) -> bytes:
    """Must match app.keywords.secrets.query_aad in the worker service."""
    return f"{tenant_id}:{site_id}:search_query:{key_version}".encode()


class KeywordService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        encryption_key: bytes | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.encryption_key = encryption_key

    async def _site_exists(self, site_id: UUID) -> bool:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        return site is not None

    async def latest_run(self, site_id: UUID) -> KeywordAnalysisRun | None:
        return await self.session.scalar(
            select(KeywordAnalysisRun)
            .where(
                KeywordAnalysisRun.tenant_id == self.context.tenant_id,
                KeywordAnalysisRun.site_id == site_id,
                KeywordAnalysisRun.status == "completed",
            )
            .order_by(KeywordAnalysisRun.created_at.desc(), KeywordAnalysisRun.id.desc())
            .limit(1)
        )

    async def list_clusters(
        self,
        site_id: UUID,
        limit: int,
        intent: str | None = None,
        answer_engine_only: bool = False,
    ) -> tuple[KeywordAnalysisRun, list[KeywordCluster]] | None:
        if not await self._site_exists(site_id):
            return None
        run = await self.latest_run(site_id)
        if run is None:
            return None
        filters = [
            KeywordCluster.tenant_id == self.context.tenant_id,
            KeywordCluster.site_id == site_id,
            KeywordCluster.analysis_run_id == run.id,
        ]
        if intent:
            filters.append(KeywordCluster.intent == intent)
        if answer_engine_only:
            filters.append(KeywordCluster.answer_engine_candidate.is_(True))
        result = await self.session.scalars(
            select(KeywordCluster)
            .where(*filters)
            .order_by(KeywordCluster.opportunity_score.desc(), KeywordCluster.cluster_key)
            .limit(min(limit, MAX_CLUSTER_PAGE_SIZE))
        )
        return run, list(result)

    async def get_cluster(self, cluster_id: UUID) -> KeywordCluster | None:
        return await self.session.scalar(
            select(KeywordCluster).where(
                KeywordCluster.id == cluster_id,
                KeywordCluster.tenant_id == self.context.tenant_id,
            )
        )

    async def list_members(self, cluster_id: UUID, limit: int) -> list[KeywordMemberRead]:
        if self.context.role not in READ_TERM_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_permissions_for_terms"
            )
        if self.encryption_key is None or len(self.encryption_key) != 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="keyword_terms_not_configured",
            )
        cluster = await self.get_cluster(cluster_id)
        if cluster is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="keyword_cluster_not_found"
            )

        rows = await self.session.execute(
            select(KeywordClusterMember, SearchQuery)
            .join(
                SearchQuery,
                (SearchQuery.tenant_id == KeywordClusterMember.tenant_id)
                & (SearchQuery.site_id == KeywordClusterMember.site_id)
                & (SearchQuery.query_hash == KeywordClusterMember.query_hash),
            )
            .where(
                KeywordClusterMember.tenant_id == self.context.tenant_id,
                KeywordClusterMember.cluster_id == cluster_id,
            )
            .order_by(KeywordClusterMember.impressions.desc(), KeywordClusterMember.query_hash)
            .limit(min(limit, MAX_MEMBER_PAGE_SIZE))
        )

        members: list[KeywordMemberRead] = []
        unreadable = 0
        for member, stored in rows.all():
            aad = query_aad(cluster.tenant_id, cluster.site_id, stored.key_version)
            if not stdlib_secrets.compare_digest(
                hashlib.sha256(aad).hexdigest(), stored.aad_hash
            ):
                unreadable += 1
                continue
            try:
                term = AESGCM(self.encryption_key).decrypt(
                    stored.nonce, stored.ciphertext, aad
                ).decode("utf-8")
            except (InvalidTag, ValueError, UnicodeDecodeError):
                # A rotated key leaves older rows unreadable; skip rather than
                # fail, and never echo the stored bytes.
                unreadable += 1
                continue
            members.append(
                KeywordMemberRead(
                    query_hash=member.query_hash,
                    term=term,
                    clicks=member.clicks,
                    impressions=member.impressions,
                    ctr=member.ctr,
                    position=member.position,
                    is_question=stored.is_question,
                    best_page_id=member.best_page_id,
                )
            )

        payload = {
            "cluster_id": str(cluster_id),
            "cluster_key": cluster.cluster_key,
            "revealed_terms": len(members),
            "unreadable_terms": unreadable,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="keyword_terms.read",
                resource_type="keyword_cluster",
                resource_id=str(cluster_id),
                trace_id=self.context.trace_id,
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.commit()
        return members
