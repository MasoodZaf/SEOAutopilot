import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CrawlCreate, SiteCreate
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, CrawlJob, OutboxEvent, Site, SiteVerificationChallenge
from app.services.verification import DnsTxtVerifier


def stable_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class SiteService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def list_sites(self) -> list[Site]:
        result = await self.session.scalars(
            select(Site)
            .where(Site.tenant_id == self.context.tenant_id)
            .order_by(Site.created_at.desc(), Site.id)
        )
        return list(result)

    async def get_site(self, site_id: UUID) -> Site | None:
        return await self.session.scalar(
            select(Site).where(Site.tenant_id == self.context.tenant_id, Site.id == site_id)
        )

    async def create_site(self, command: SiteCreate) -> Site:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER)
        host = command.canonical_origin.split("://", 1)[1]
        site = Site(
            tenant_id=self.context.tenant_id,
            name=command.name.strip(),
            canonical_origin=command.canonical_origin,
            normalized_host=host,
            mode=command.mode.value,
        )
        self.session.add(site)
        await self.session.flush()
        payload = {"site_id": str(site.id), "mode": site.mode, "status": site.status}
        event_hash = stable_hash({**payload, "actor_id": str(self.context.actor_id)})
        self.session.add_all(
            [
                AuditEvent(
                    tenant_id=self.context.tenant_id,
                    actor_type="user",
                    actor_id=str(self.context.actor_id),
                    action="site.created",
                    resource_type="site",
                    resource_id=str(site.id),
                    trace_id=self.context.trace_id,
                    metadata_json={"mode": site.mode},
                    event_hash=event_hash,
                ),
                OutboxEvent(
                    tenant_id=self.context.tenant_id,
                    event_type="site.created",
                    event_version=1,
                    aggregate_type="site",
                    aggregate_id=site.id,
                    payload=payload,
                ),
            ]
        )
        return site

    async def create_verification_challenge(
        self, site_id: UUID
    ) -> tuple[SiteVerificationChallenge, str]:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER)
        site = await self.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        token = secrets.token_urlsafe(32)
        challenge = SiteVerificationChallenge(
            tenant_id=self.context.tenant_id,
            site_id=site.id,
            method="dns_txt",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            created_by=self.context.actor_id,
        )
        self.session.add(challenge)
        await self.session.flush()
        self._stage_event(
            "site.verification_challenge_created",
            "verification_challenge",
            challenge.id,
            {"site_id": str(site.id), "method": "dns_txt"},
        )
        return challenge, token

    async def verify_site(self, site_id: UUID, token: str, verifier: DnsTxtVerifier) -> Site:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER)
        site = await self.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        challenge = await self.session.scalar(
            select(SiteVerificationChallenge).where(
                SiteVerificationChallenge.tenant_id == self.context.tenant_id,
                SiteVerificationChallenge.site_id == site.id,
                SiteVerificationChallenge.token_hash == token_hash,
                SiteVerificationChallenge.status == "pending",
                SiteVerificationChallenge.expires_at > datetime.now(UTC),
            )
        )
        if challenge is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="verification_challenge_invalid"
            )
        if not await verifier.verify(site.normalized_host, token):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="dns_proof_not_found")
        now = datetime.now(UTC)
        challenge.status = "verified"
        challenge.verified_at = now
        site.status = "active"
        site.verified_at = now
        site.version += 1
        self._stage_event(
            "site.verified",
            "site",
            site.id,
            {"site_id": str(site.id), "method": "dns_txt"},
        )
        return site

    async def create_crawl(self, site_id: UUID, command: CrawlCreate) -> CrawlJob:
        self.context.require(Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.DEVELOPER)
        site = await self.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        if site.verified_at is None or site.status != "active":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="site_not_verified")
        active_crawl = await self.session.scalar(
            select(CrawlJob).where(
                CrawlJob.tenant_id == self.context.tenant_id,
                CrawlJob.site_id == site.id,
                CrawlJob.status.in_(("queued", "running")),
            )
        )
        if active_crawl is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="crawl_already_active")
        crawl = CrawlJob(
            tenant_id=self.context.tenant_id,
            site_id=site.id,
            kind=command.kind,
            status="queued",
            requested_by=self.context.actor_id,
            config_snapshot={
                "max_pages": command.max_pages,
                "max_depth": command.max_depth,
                "render_policy": command.render_policy,
                "canonical_origin": site.canonical_origin,
            },
        )
        try:
            async with self.session.begin_nested():
                self.session.add(crawl)
                await self.session.flush()
        except IntegrityError as error:
            constraint = getattr(getattr(error, "orig", None), "constraint_name", None)
            if constraint == "crawl_job_one_active_per_site_idx":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="crawl_already_active"
                ) from error
            raise
        self._stage_event(
            "crawl.requested",
            "crawl_job",
            crawl.id,
            {"crawl_id": str(crawl.id), "site_id": str(site.id)},
        )
        return crawl

    async def latest_crawl(self, site_id: UUID) -> CrawlJob | None:
        site = await self.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        return await self.session.scalar(
            select(CrawlJob)
            .where(
                CrawlJob.tenant_id == self.context.tenant_id,
                CrawlJob.site_id == site.id,
            )
            .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
            .limit(1)
        )

    def _stage_event(
        self, event_type: str, resource_type: str, resource_id: UUID, payload: dict[str, object]
    ) -> None:
        event_hash = stable_hash(
            {**payload, "event_type": event_type, "actor_id": str(self.context.actor_id)}
        )
        self.session.add_all(
            [
                AuditEvent(
                    tenant_id=self.context.tenant_id,
                    actor_type="user",
                    actor_id=str(self.context.actor_id),
                    action=event_type,
                    resource_type=resource_type,
                    resource_id=str(resource_id),
                    trace_id=self.context.trace_id,
                    metadata_json=payload,
                    event_hash=event_hash,
                ),
                OutboxEvent(
                    tenant_id=self.context.tenant_id,
                    event_type=event_type,
                    event_version=1,
                    aggregate_type=resource_type,
                    aggregate_id=resource_id,
                    payload=payload,
                ),
            ]
        )
