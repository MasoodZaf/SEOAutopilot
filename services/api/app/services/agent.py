"""Executes a routed skill and records the conversation.

Every answer is assembled from stored records through the same services the REST
API uses, and carries the references it was built from. A skill re-checks the
actor's role before running, so the workspace can never widen what an actor can
reach. Nothing here writes site content or reaches a deployment adapter.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RoutineUpsert
from app.core.context import Role, TenantContext
from app.db.models import AgentMessage, AgentSession, AgentTask, Routine, Site
from app.domain.routines import Cadence, RoutineSchedule, initial_run_at
from app.domain.skills import SKILLS, Skill, SkillEffect, route
from app.services.briefs import ContentBriefService
from app.services.competitors import CompetitorService
from app.services.keywords import KeywordService
from app.services.opportunities import OpportunityService
from app.services.reports import ReportService
from app.services.routines import RoutineService

MAX_SESSIONS_PER_SITE = 100
MAX_MESSAGE_PAGE_SIZE = 200
SUMMARY_LIMIT = 5


class SkillAnswer:
    """An agent reply plus the stored records it was built from."""

    def __init__(self, body: str, evidence: list[dict[str, Any]] | None = None) -> None:
        self.body = body
        self.evidence = evidence or []


def _bullet(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def _no_evidence(what: str, how: str) -> SkillAnswer:
    return SkillAnswer(f"There is no {what} stored for this site yet. {how}")


class AgentService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        keyword_encryption_key: bytes | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.keyword_encryption_key = keyword_encryption_key

    # --- sessions -----------------------------------------------------------

    async def _require_site(self, site_id: UUID) -> Site:
        site = await self.session.scalar(
            select(Site).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
        return site

    async def list_sessions(self, site_id: UUID) -> list[AgentSession] | None:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            return None
        result = await self.session.scalars(
            select(AgentSession)
            .where(
                AgentSession.tenant_id == self.context.tenant_id,
                AgentSession.site_id == site_id,
                AgentSession.status == "active",
            )
            .order_by(AgentSession.updated_at.desc(), AgentSession.id.desc())
            .limit(50)
        )
        return list(result)

    async def create_session(self, site_id: UUID, title: str) -> AgentSession:
        await self._require_site(site_id)
        existing = await self.session.scalar(
            select(func.count())
            .select_from(AgentSession)
            .where(
                AgentSession.tenant_id == self.context.tenant_id,
                AgentSession.site_id == site_id,
                AgentSession.status == "active",
            )
        )
        if (existing or 0) >= MAX_SESSIONS_PER_SITE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="agent_session_limit_reached"
            )
        record = AgentSession(
            tenant_id=self.context.tenant_id,
            site_id=site_id,
            title=title.strip()[:200] or "New conversation",
            created_by=self.context.actor_id,
        )
        self.session.add(record)
        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def get_session(self, session_id: UUID) -> AgentSession | None:
        return await self.session.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.tenant_id == self.context.tenant_id,
            )
        )

    async def list_messages(self, session_id: UUID, limit: int) -> list[AgentMessage]:
        result = await self.session.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.tenant_id == self.context.tenant_id,
                AgentMessage.session_id == session_id,
            )
            .order_by(AgentMessage.sequence)
            .limit(min(limit, MAX_MESSAGE_PAGE_SIZE))
        )
        return list(result)

    async def list_tasks(self, site_id: UUID, limit: int) -> list[AgentTask] | None:
        site = await self.session.scalar(
            select(Site.id).where(Site.id == site_id, Site.tenant_id == self.context.tenant_id)
        )
        if site is None:
            return None
        result = await self.session.scalars(
            select(AgentTask)
            .where(AgentTask.tenant_id == self.context.tenant_id, AgentTask.site_id == site_id)
            .order_by(AgentTask.created_at.desc(), AgentTask.id.desc())
            .limit(min(limit, 100))
        )
        return list(result)

    # --- conversation -------------------------------------------------------

    async def post_message(
        self, session_id: UUID, body: str, forced_skill_key: str | None = None
    ) -> tuple[AgentMessage, AgentMessage, AgentTask | None]:
        record = await self.get_session(session_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="agent_session_not_found"
            )
        site = await self._require_site(record.site_id)

        next_sequence = (
            await self.session.scalar(
                select(func.coalesce(func.max(AgentMessage.sequence), 0)).where(
                    AgentMessage.tenant_id == self.context.tenant_id,
                    AgentMessage.session_id == session_id,
                )
            )
        ) or 0
        user_message = AgentMessage(
            tenant_id=self.context.tenant_id,
            session_id=session_id,
            site_id=record.site_id,
            sequence=next_sequence + 1,
            role="user",
            body=body.strip()[:8000],
        )
        self.session.add(user_message)
        await self.session.flush()

        skill, answer = await self._resolve(body, forced_skill_key)
        task: AgentTask | None = None
        if skill is not None:
            task, answer = await self._execute(skill, site, session_id, answer)

        agent_message = AgentMessage(
            tenant_id=self.context.tenant_id,
            session_id=session_id,
            site_id=record.site_id,
            sequence=next_sequence + 2,
            role="agent",
            body=answer.body[:8000],
            skill_key=skill.key if skill else None,
            agent_task_id=task.id if task else None,
            evidence_json=answer.evidence,
        )
        self.session.add(agent_message)
        record.message_count = next_sequence + 2
        record.updated_at = datetime.now(UTC)
        await self.session.flush()
        await self.session.refresh(user_message)
        await self.session.refresh(agent_message)
        if task is not None:
            await self.session.refresh(task)
        return user_message, agent_message, task

    async def _resolve(
        self, body: str, forced_skill_key: str | None
    ) -> tuple[Skill | None, SkillAnswer]:
        if forced_skill_key:
            skill = next((item for item in SKILLS if item.key == forced_skill_key), None)
            if skill is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="skill_not_found"
                )
            if self.context.role not in skill.allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="skill_not_permitted_for_role"
                )
            return skill, SkillAnswer("")

        result = route(body, self.context.role)
        if result.skill is not None:
            return result.skill, SkillAnswer("")

        # Nothing matched clearly enough. Offer choices rather than guessing at
        # an action, especially one that would schedule work.
        options = result.alternatives[:4] or tuple(
            item for item in SKILLS if self.context.role in item.allowed_roles
        )[:4]
        listed = _bullet([f"**{item.name}** — {item.example}" for item in options])
        return None, SkillAnswer(
            "I could not match that to one thing I can do for certain, so I have not "
            f"started anything. Did you mean one of these?\n\n{listed}"
        )

    async def _execute(
        self, skill: Skill, site: Site, session_id: UUID, answer: SkillAnswer
    ) -> tuple[AgentTask, SkillAnswer]:
        if self.context.role not in skill.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="skill_not_permitted_for_role"
            )
        task = AgentTask(
            tenant_id=self.context.tenant_id,
            session_id=session_id,
            site_id=site.id,
            skill_key=skill.key,
            status="running",
            requested_by=self.context.actor_id,
        )
        self.session.add(task)
        await self.session.flush()

        try:
            if skill.effect is SkillEffect.SCHEDULE:
                answer, routine_run_id = await self._schedule(skill, site)
                task.routine_run_id = routine_run_id
            else:
                answer = await self._read(skill, site)
        except HTTPException as error:
            task.status = "blocked"
            task.error_code = str(error.detail)[:80]
            task.finished_at = datetime.now(UTC)
            return task, SkillAnswer(
                f"I could not run **{skill.name}**: {error.detail}."
            )

        task.status = "completed"
        task.finished_at = datetime.now(UTC)
        task.result_json = {"evidence_count": len(answer.evidence)}
        return task, answer

    async def _schedule(self, skill: Skill, site: Site) -> tuple[SkillAnswer, UUID]:
        assert skill.routine_kind is not None
        routines = RoutineService(self.session, self.context)
        routine = await self.session.scalar(
            select(Routine).where(
                Routine.tenant_id == self.context.tenant_id,
                Routine.site_id == site.id,
                Routine.kind == skill.routine_kind,
            )
        )
        if routine is None:
            # An ad-hoc invocation creates the routine parked, never enabled:
            # asking for one run must not silently start a recurring schedule.
            schedule = RoutineSchedule(cadence=Cadence.DAILY, hour_utc=6)
            routine = Routine(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                kind=skill.routine_kind,
                cadence=Cadence.DAILY.value,
                schedule_hour_utc=6,
                schedule_minute_utc=0,
                enabled=False,
                next_run_at=initial_run_at(schedule),
                created_by=self.context.actor_id,
            )
            self.session.add(routine)
            await self.session.flush()

        run = await routines.trigger(routine.id)
        return (
            SkillAnswer(
                f"Queued **{skill.name}**. It runs in the worker under the same checks as a "
                "scheduled run: it is skipped if the site is unverified or frozen, and it "
                f"cannot deploy anything. This routine is not on a schedule yet — enable "
                f"the `{skill.routine_kind}` routine if you want it to repeat.",
                [{"kind": "routine_run", "id": str(run.id), "routine_kind": skill.routine_kind}],
            ),
            run.id,
        )

    async def _read(self, skill: Skill, site: Site) -> SkillAnswer:
        handlers = {
            "site_audit": self._read_site_audit,
            "keyword_research": self._read_keywords,
            "content_briefs": self._read_briefs,
            "sitemap_review": self._read_sitemap,
            "competitor_pages": self._read_competitors,
            "ai_visibility": self._read_ai_visibility,
            "weekly_report": self._read_weekly_report,
            "routines": self._read_routines,
        }
        handler = handlers.get(skill.key)
        if handler is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="skill_not_implemented"
            )
        return await handler(site)

    async def _read_site_audit(self, site: Site) -> SkillAnswer:
        service = OpportunityService(self.session, self.context)
        opportunities = await service.list_top(site.id, SUMMARY_LIMIT, "open")
        if not opportunities:
            return _no_evidence(
                "open finding", "Run a crawl first, then ask again."
            )
        urls = await service.page_urls(site.id, opportunities)
        lines = [
            f"{item.title} — {urls.get(item.page_id, 'unknown URL')} "
            f"(score {item.score:.1f}, {item.risk} risk)"
            for item in opportunities
        ]
        return SkillAnswer(
            f"The top {len(opportunities)} open findings for {site.name}:\n\n{_bullet(lines)}\n\n"
            "Each is advisory. Nothing changes on the site without a reviewed proposal.",
            [{"kind": "opportunity", "id": str(item.id)} for item in opportunities],
        )

    async def _read_keywords(self, site: Site) -> SkillAnswer:
        service = KeywordService(
            self.session, self.context, encryption_key=self.keyword_encryption_key
        )
        found = await service.list_clusters(site.id, SUMMARY_LIMIT)
        if found is None:
            return _no_evidence(
                "keyword analysis",
                "Connect Search Console and run the keyword refresh routine.",
            )
        run, clusters = found
        if not clusters:
            return _no_evidence("keyword cluster", "The last analysis produced none.")
        def describe(item: Any) -> str:
            base = (
                f"**{item.label}** — {item.intent}, {item.member_count} queries, "
                f"{item.impressions:.0f} impressions"
            )
            if item.average_position is not None:
                base += f", avg position {item.average_position:.1f}"
            if item.striking_distance_count:
                base += f", {item.striking_distance_count} in striking distance"
            return base

        lines = [describe(item) for item in clusters]
        answer_engine = sum(1 for item in clusters if item.answer_engine_candidate)
        return SkillAnswer(
            f"Top {len(clusters)} keyword clusters for {site.name} "
            f"({run.window_start} to {run.window_end}, {run.queries_considered} queries):\n\n"
            f"{_bullet(lines)}\n\n{answer_engine} of these are answer-engine candidates.",
            [{"kind": "keyword_cluster", "id": str(item.id)} for item in clusters],
        )

    async def _read_briefs(self, site: Site) -> SkillAnswer:
        briefs = await ContentBriefService(self.session, self.context).list_for_site(
            site.id, SUMMARY_LIMIT, "queued"
        )
        if not briefs:
            return _no_evidence(
                "queued content brief", "Run the content briefs routine after a keyword refresh."
            )
        lines = [
            f"**{item.cluster_label}** — {item.kind.replace('_', ' ')}, "
            f"priority {item.priority_score:.0f}"
            for item in briefs
        ]
        return SkillAnswer(
            f"{len(briefs)} briefs at the top of the queue:\n\n{_bullet(lines)}",
            [{"kind": "content_brief", "id": str(item.id)} for item in briefs],
        )

    async def _read_sitemap(self, site: Site) -> SkillAnswer:
        report = await ReportService(self.session, self.context).latest(
            site.id, "sitemap_coverage"
        )
        if report is None:
            return _no_evidence(
                "sitemap coverage report", "Run the sitemap coverage routine after a crawl."
            )
        totals = report.payload_json.get("totals", {}) if report.payload_json else {}
        lines = [
            f"{totals.get('declared_in_scope', 0)} URLs declared in scope",
            f"{totals.get('declared_and_crawled', 0)} of those reached by the crawl",
            f"{totals.get('declared_not_crawled', 0)} declared but never crawled",
            (
                f"{totals.get('indexable_missing_from_sitemap', 0)} indexable pages "
                "missing from the sitemap"
            ),
        ]
        return SkillAnswer(
            f"Sitemap coverage as of {report.period_end}:\n\n{_bullet(lines)}",
            [{"kind": "report", "id": str(report.id)}],
        )

    async def _read_competitors(self, site: Site) -> SkillAnswer:
        found = await CompetitorService(self.session, self.context).latest_scan(site.id)
        if found is None:
            return _no_evidence(
                "competitor scan",
                "Add a competitor and the pages to track, then run the competitor scan.",
            )
        scan, observations = found
        observed = [item for item in observations if item.outcome == "observed"]
        lines = [
            (
                f"{item.title or 'untitled'} — {item.word_count} words, "
                f"{item.heading_count} headings, schema: "
                f"{', '.join(item.structured_data_types) or 'none'}"
            )
            for item in observed[:SUMMARY_LIMIT]
        ]
        blocked = scan.pages_blocked
        return SkillAnswer(
            f"Last competitor scan reached {scan.pages_observed} of {scan.pages_requested} "
            f"tracked pages"
            + (f", {blocked} blocked by robots" if blocked else "")
            + (f":\n\n{_bullet(lines)}" if lines else "."),
            [{"kind": "competitor_scan", "id": str(scan.id)}],
        )

    async def _read_ai_visibility(self, site: Site) -> SkillAnswer:
        snapshots = await CompetitorService(self.session, self.context).ai_visibility_history(
            site.id, 2
        )
        if not snapshots:
            return _no_evidence(
                "answer-engine readiness snapshot", "Run the AI visibility routine after a crawl."
            )
        latest = snapshots[0]
        factors = latest.factors_json.get("factors", {}) if latest.factors_json else {}
        lines = [
            f"{name.replace('_', ' ')}: "
            + (
                f"{float(detail.get('value', 0)) * 100:.0f}%"
                if isinstance(detail, dict) and detail.get("measured")
                else "not measured"
            )
            for name, detail in factors.items()
        ]
        trend = ""
        if len(snapshots) > 1:
            delta = latest.readiness_score - snapshots[1].readiness_score
            trend = f" ({delta:+.1f} since {snapshots[1].captured_on})"
        return SkillAnswer(
            f"Answer-engine readiness for {site.name} is "
            f"{latest.readiness_score:.0f}/100{trend}:\n\n{_bullet(lines)}\n\n"
            "This measures how well the site is positioned to be cited. It does not "
            "observe what any answer engine actually said; that needs a certified "
            "provider, which is not connected.",
            [{"kind": "ai_visibility_snapshot", "id": str(latest.id)}],
        )

    async def _read_weekly_report(self, site: Site) -> SkillAnswer:
        report = await ReportService(self.session, self.context).latest(site.id, "weekly_digest")
        if report is None:
            return _no_evidence("weekly report", "Run the weekly report routine.")
        payload = report.payload_json or {}
        opportunities = payload.get("opportunities", {})
        search = payload.get("search", {})
        lines = [
            (
                f"{opportunities.get('opened_in_period', 0)} findings opened, "
                f"{opportunities.get('resolved_in_period', 0)} resolved"
            ),
            f"{len(opportunities.get('top', []) or [])} in the review queue",
        ]
        if search.get("available"):
            clicks = search.get("clicks", {})
            lines.append(
                f"search clicks {clicks.get('current')} vs {clicks.get('previous')} "
                f"in the prior window (association, not attribution)"
            )
        else:
            lines.append("no Search Console evidence in the window")
        return SkillAnswer(
            f"Weekly digest for {report.period_start} to {report.period_end}:\n\n{_bullet(lines)}",
            [{"kind": "report", "id": str(report.id)}],
        )

    async def _read_routines(self, site: Site) -> SkillAnswer:
        routines = await RoutineService(self.session, self.context).list_for_site(site.id)
        if not routines:
            return SkillAnswer(
                f"No routines are scheduled for {site.name} yet. Ask me to run an audit, "
                "recluster keywords, or scan competitors and I will queue it once; you can "
                "then enable that routine to make it repeat."
            )
        lines = [
            f"**{item.kind}** — {item.cadence}, "
            + ("enabled" if item.enabled else "not scheduled")
            + f", next {item.next_run_at:%Y-%m-%d %H:%M} UTC"
            + (f", last run {item.last_status}" if item.last_status else "")
            for item in routines
        ]
        return SkillAnswer(
            f"{len(routines)} routines for {site.name}:\n\n{_bullet(lines)}",
            [{"kind": "routine", "id": str(item.id)} for item in routines],
        )


def visible_skills(role: Role) -> list[Skill]:
    return [skill for skill in SKILLS if role in skill.allowed_roles]


def routine_upsert_for(skill: Skill) -> RoutineUpsert | None:
    """Convenience for turning an ad-hoc skill into a scheduled routine."""
    if skill.routine_kind is None:
        return None
    return RoutineUpsert(kind=skill.routine_kind, cadence="daily")  # type: ignore[arg-type]
