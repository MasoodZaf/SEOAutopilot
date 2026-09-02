from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RoutineUpsert
from app.core.context import Role, TenantContext
from app.db.models import AuditEvent, OutboxEvent, Routine, RoutineRun, Site
from app.domain.routines import Cadence, RoutineSchedule, initial_run_at
from app.services.opportunities import stable_hash

MANAGE_ROUTINE_ROLES = {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER}
MAX_RUN_PAGE_SIZE = 100


def schedule_from_command(command: RoutineUpsert) -> RoutineSchedule:
    return RoutineSchedule(
        cadence=Cadence(command.cadence.value),
        hour_utc=command.schedule_hour_utc,
        minute_utc=command.schedule_minute_utc,
        isodow=command.schedule_isodow,
        dom=command.schedule_dom,
    )


class RoutineService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def _require_schedulable_site(self, site_id: UUID) -> Site:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        # Ownership proof gates every recurring action, exactly as it gates an
        # on-demand crawl. A routine must not become a path around verification.
        if site.status != "active" or site.verified_at is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="site_not_verified"
            )
        return site

    async def list_for_site(self, site_id: UUID) -> list[Routine] | None:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            return None
        result = await self.session.scalars(
            select(Routine)
            .where(Routine.tenant_id == self.context.tenant_id, Routine.site_id == site_id)
            .order_by(Routine.kind)
        )
        return list(result)

    async def upsert(self, site_id: UUID, command: RoutineUpsert) -> Routine:
        if self.context.role not in MANAGE_ROUTINE_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_permissions_for_routine"
            )
        await self._require_schedulable_site(site_id)
        schedule = schedule_from_command(command)
        now = datetime.now(UTC)

        routine = await self.session.scalar(
            select(Routine).where(
                Routine.tenant_id == self.context.tenant_id,
                Routine.site_id == site_id,
                Routine.kind == command.kind.value,
            )
        )
        created = routine is None
        if routine is None:
            routine = Routine(
                tenant_id=self.context.tenant_id,
                site_id=site_id,
                kind=command.kind.value,
                created_by=self.context.actor_id,
                next_run_at=initial_run_at(schedule, now),
            )
            self.session.add(routine)

        routine.cadence = command.cadence.value
        routine.schedule_hour_utc = command.schedule_hour_utc
        routine.schedule_minute_utc = command.schedule_minute_utc
        routine.schedule_isodow = command.schedule_isodow
        routine.schedule_dom = command.schedule_dom
        routine.enabled = command.enabled
        routine.updated_at = now
        if not created:
            # A rescheduled routine re-anchors from now so an edit never
            # replays an already-passed slot.
            routine.next_run_at = initial_run_at(schedule, now)
            routine.version += 1

        payload = {
            "kind": routine.kind,
            "cadence": routine.cadence,
            "enabled": routine.enabled,
            "next_run_at": routine.next_run_at.isoformat(),
        }
        self.session.add(
            AuditEvent(
                tenant_id=self.context.tenant_id,
                actor_type="user",
                actor_id=str(self.context.actor_id),
                action="routine.created" if created else "routine.updated",
                resource_type="routine",
                resource_id=str(routine.id),
                trace_id=self.context.trace_id,
                metadata_json=payload,
                event_hash=stable_hash({**payload, "actor_id": str(self.context.actor_id)}),
            )
        )
        await self.session.flush()
        await self.session.refresh(routine)
        return routine

    async def trigger(self, routine_id: UUID) -> RoutineRun:
        """Queue an out-of-band run without disturbing the schedule."""
        if self.context.role not in MANAGE_ROUTINE_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_permissions_for_routine"
            )
        routine = await self.session.scalar(
            select(Routine).where(
                Routine.id == routine_id, Routine.tenant_id == self.context.tenant_id
            )
        )
        if routine is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="routine_not_found")
        await self._require_schedulable_site(routine.site_id)

        # Truncating to the minute makes a double-click idempotent through the
        # (routine_id, scheduled_for) uniqueness constraint.
        slot = datetime.now(UTC).replace(second=0, microsecond=0)
        run = RoutineRun(
            tenant_id=self.context.tenant_id,
            routine_id=routine.id,
            site_id=routine.site_id,
            kind=routine.kind,
            trigger="manual",
            scheduled_for=slot,
        )
        self.session.add(run)
        try:
            await self.session.flush()
        except IntegrityError as error:
            await self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="routine_run_already_queued"
            ) from error

        self.session.add(
            OutboxEvent(
                tenant_id=self.context.tenant_id,
                event_type="routine.run.queued.v1",
                event_version=1,
                aggregate_type="routine_run",
                aggregate_id=run.id,
                payload={
                    "routine_run_id": str(run.id),
                    "routine_id": str(routine.id),
                    "site_id": str(routine.site_id),
                    "kind": routine.kind,
                    "trigger": "manual",
                },
            )
        )
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def list_runs(self, site_id: UUID, limit: int) -> list[RoutineRun] | None:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            return None
        result = await self.session.scalars(
            select(RoutineRun)
            .where(RoutineRun.tenant_id == self.context.tenant_id, RoutineRun.site_id == site_id)
            .order_by(RoutineRun.created_at.desc(), RoutineRun.id.desc())
            .limit(min(limit, MAX_RUN_PAGE_SIZE))
        )
        return list(result)
