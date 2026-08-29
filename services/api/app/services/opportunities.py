import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import OpportunitySuppress
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, Opportunity, OutboxEvent, Page, Site

ALLOWED_SUPPRESSION_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER}


def stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def build_diverse_top_query(
    *,
    tenant_id: UUID,
    site_id: UUID,
    opportunity_status: str,
    limit: int,
    opportunity_type: str | None = None,
    min_score: float | None = None,
) -> Select[tuple[Opportunity]]:
    """Rank repeated rule/page opportunities in deterministic rounds.

    A high-volume rule must not monopolize the decision list. The public API
    remains page-level, while the ranking returns the best page for each
    distinct opportunity type/title before the second page for each, and so on.
    """
    filters = [
        Opportunity.tenant_id == tenant_id,
        Opportunity.site_id == site_id,
        Opportunity.status == opportunity_status,
        Opportunity.risk != "prohibited",
    ]
    if opportunity_status != "suppressed":
        filters.append(Opportunity.suppressed_reason.is_(None))
    if opportunity_type:
        filters.append(Opportunity.type == opportunity_type)
    if min_score is not None:
        filters.append(Opportunity.score >= min_score)

    ranked = (
        select(
            Opportunity.id.label("opportunity_id"),
            func.row_number()
            .over(
                partition_by=(Opportunity.type, Opportunity.title),
                order_by=(
                    Opportunity.score.desc(),
                    Opportunity.fingerprint,
                    Opportunity.id,
                ),
            )
            .label("diversity_rank"),
        )
        .where(*filters)
        .subquery()
    )
    return (
        select(Opportunity)
        .join(ranked, ranked.c.opportunity_id == Opportunity.id)
        .where(
            Opportunity.tenant_id == tenant_id,
            Opportunity.site_id == site_id,
        )
        .order_by(
            ranked.c.diversity_rank,
            Opportunity.score.desc(),
            Opportunity.fingerprint,
            Opportunity.id,
        )
        .limit(limit)
    )


def build_page_url_query(
    *, tenant_id: UUID, site_id: UUID, page_ids: list[UUID]
) -> Select[tuple[UUID, str]]:
    return select(Page.id, Page.normalized_url).where(
        Page.tenant_id == tenant_id,
        Page.site_id == site_id,
        Page.id.in_(page_ids),
    )


class OpportunityService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def list_top(
        self,
        site_id: UUID,
        limit: int,
        opportunity_status: str,
        opportunity_type: str | None = None,
        min_score: float | None = None,
    ) -> list[Opportunity] | None:
        site = await self.session.scalar(
            select(Site.id).where(
                Site.id == site_id,
                Site.tenant_id == self.context.tenant_id,
            )
        )
        if site is None:
            return None
        result = await self.session.scalars(
            build_diverse_top_query(
                tenant_id=self.context.tenant_id,
                site_id=site_id,
                opportunity_status=opportunity_status,
                limit=limit,
                opportunity_type=opportunity_type,
                min_score=min_score,
            )
        )
        return list(result)

    async def page_urls(self, site_id: UUID, opportunities: list[Opportunity]) -> dict[UUID, str]:
        page_ids = list({item.page_id for item in opportunities})
        if not page_ids:
            return {}
        rows = await self.session.execute(
            build_page_url_query(
                tenant_id=self.context.tenant_id,
                site_id=site_id,
                page_ids=page_ids,
            )
        )
        return {page_id: normalized_url for page_id, normalized_url in rows.all()}

    async def get(self, opportunity_id: UUID) -> Opportunity | None:
        return await self.session.scalar(
            select(Opportunity).where(
                Opportunity.id == opportunity_id,
                Opportunity.tenant_id == self.context.tenant_id,
            )
        )

    async def suppress(
        self,
        opportunity_id: UUID,
        command: OpportunitySuppress,
    ) -> Opportunity:
        if self.context.role not in ALLOWED_SUPPRESSION_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_suppression",
            )
        opportunity = await self.get(opportunity_id)
        if opportunity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="opportunity_not_found",
            )

        now = datetime.now(UTC)
        opportunity.status = "suppressed"
        opportunity.suppressed_reason = command.reason
        opportunity.suppressed_at = now
        opportunity.suppressed_by = self.context.actor_id
        opportunity.updated_at = now

        event_payload = {
            "reason": command.reason,
            "notes": command.notes,
            "previous_score": opportunity.score,
            "type": opportunity.type,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="opportunity.suppressed",
                resource_type="opportunity",
                resource_id=str(opportunity.id),
                trace_id=self.context.trace_id,
                metadata_json=event_payload,
                event_hash=stable_hash({**event_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        self.session.add(
            OutboxEvent(
                tenant_id=self.context.tenant_id,
                event_type="opportunity.suppressed.v1",
                event_version=1,
                aggregate_type="opportunity",
                aggregate_id=opportunity.id,
                payload={
                    "opportunity_id": str(opportunity.id),
                    "site_id": str(opportunity.site_id),
                    "reason": command.reason,
                },
            )
        )
        await self.session.commit()
        await self.session.refresh(opportunity)
        return opportunity

    async def unsuppress(self, opportunity_id: UUID) -> Opportunity:
        if self.context.role not in ALLOWED_SUPPRESSION_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_permissions_for_suppression",
            )
        opportunity = await self.get(opportunity_id)
        if opportunity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="opportunity_not_found",
            )

        now = datetime.now(UTC)
        opportunity.status = "open"
        opportunity.suppressed_reason = None
        opportunity.suppressed_at = None
        opportunity.suppressed_by = None
        opportunity.updated_at = now

        unsuppress_payload = {
            "previous_status": "suppressed",
            "type": opportunity.type,
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="opportunity.unsuppressed",
                resource_type="opportunity",
                resource_id=str(opportunity.id),
                trace_id=self.context.trace_id,
                metadata_json=unsuppress_payload,
                event_hash=stable_hash({**unsuppress_payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        self.session.add(
            OutboxEvent(
                tenant_id=self.context.tenant_id,
                event_type="opportunity.unsuppressed.v1",
                event_version=1,
                aggregate_type="opportunity",
                aggregate_id=opportunity.id,
                payload={
                    "opportunity_id": str(opportunity.id),
                    "site_id": str(opportunity.site_id),
                },
            )
        )
        await self.session.commit()
        await self.session.refresh(opportunity)
        return opportunity
