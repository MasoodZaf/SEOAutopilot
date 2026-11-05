"""Executes a routed skill and records the conversation.

Every answer is assembled from stored records through the same services the REST
API uses, and carries the references it was built from. A skill re-checks the
actor's role before running, so the workspace can never widen what an actor can
reach. Nothing here writes site content or reaches a deployment adapter.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RoutineUpsert
from app.core.config import Settings, get_settings
from app.core.context import Role, TenantContext
from app.db.models import AgentMessage, AgentSession, AgentTask, Routine, SearchMetric, Site
from app.domain.github_adapter import GitHubDeploymentAdapter
from app.domain.routines import PAID_ROUTINE_KINDS, Cadence, RoutineSchedule, initial_run_at
from app.domain.skills import SKILLS, RequestedCadence, Skill, SkillEffect, parse_cadence, route
from app.services.agent_model import (
    ANSWER_SCHEMA,
    ANSWER_SYSTEM,
    PLAN_SCHEMA,
    PLAN_SYSTEM,
    AnthropicChatModel,
    ChatModel,
    ChatModelError,
    OpenAIChatModel,
)
from app.services.ai_citations import AiCitationService
from app.services.briefs import ContentBriefService
from app.services.competitors import CompetitorService
from app.services.github_connector import credential_for_site
from app.services.keywords import KeywordService
from app.services.opportunities import OpportunityService
from app.services.proposal_drafts import ProposalDraftService
from app.services.proposals import ProposalService
from app.services.reports import ReportService
from app.services.routines import RoutineService
from app.services.search_performance import SearchPerformanceService
from app.services.tenant_credentials import ANTHROPIC_API_KEY, OPENAI_API_KEY, store_for

MAX_SESSIONS_PER_SITE = 100
# Early enough to have finished before a working day starts in most timezones.
DEFAULT_SCHEDULE_HOUR_UTC = 6
MAX_MESSAGE_PAGE_SIZE = 200
SUMMARY_LIMIT = 5
# How many opportunities one request will consider, and how many drafts it will
# list back. Bounded so a single message cannot open an unbounded number of
# proposals, which is the closest a skill can come to acting at scale.
PROPOSE_LIMIT = 50
PROPOSE_REPORT_LIMIT = 10
# How much of the conversation the model sees, and how many skills one message
# may read. Bounded so a single message has a bounded cost.
CHAT_HISTORY_MESSAGES = 12
CHAT_MAX_READS = 3
CHAT_MAX_SUGGESTIONS = 3
CHAT_PROVIDERS = (("anthropic", ANTHROPIC_API_KEY), ("openai", OPENAI_API_KEY))


class SkillAnswer:
    """An agent reply plus the stored records it was built from."""

    def __init__(self, body: str, evidence: list[dict[str, Any]] | None = None) -> None:
        self.body = body
        self.evidence = evidence or []
        # Set when a model wrote the answer.
        self.provider: str | None = None
        self.model: str | None = None
        self.cost_micros = 0


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
        settings: "Settings | None" = None,
        chat_models: dict[str, ChatModel] | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.keyword_encryption_key = keyword_encryption_key
        self._settings = settings
        self.chat_models: dict[str, ChatModel] = chat_models or {
            "anthropic": AnthropicChatModel(),
            "openai": OpenAIChatModel(),
        }

    @property
    def settings(self) -> "Settings":
        return self._settings if self._settings is not None else get_settings()

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

        skill: Skill | None = None
        task: AgentTask | None = None
        answer: SkillAnswer | None = None
        if not forced_skill_key:
            answer, task = await self._converse(site, session_id, body, next_sequence)
        if answer is None:
            skill, answer = await self._resolve(body, forced_skill_key)
            if skill is not None:
                task, answer = await self._execute(skill, site, session_id, answer, body)

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
            provider=answer.provider,
            model=answer.model,
            cost_micros=answer.cost_micros,
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

    async def _chat_key(self) -> tuple[str, str] | None:
        """The workspace's own model key, Claude first. None means use the router."""
        if not self.settings.agent_chat_model_enabled:
            return None
        try:
            store = store_for(self.session, self.settings)
        except HTTPException:
            return None
        for provider, credential in CHAT_PROVIDERS:
            secret = await store.get(self.context.tenant_id, credential)
            key = secret.get("api_key") if isinstance(secret, dict) else None
            if isinstance(key, str) and key:
                return provider, key
        return None

    async def _converse(
        self, site: Site, session_id: UUID, body: str, before_sequence: int
    ) -> tuple[SkillAnswer | None, AgentTask | None]:
        """Answer in plain language through the workspace's own model key.

        Returns (None, None) when there is no key, the budget is spent, or the
        provider fails, and the caller falls back to the deterministic router.
        The model only chooses which READ skills run; every action it suggests
        comes back as a button for a person to press.
        """
        found = await self._chat_key()
        if found is None:
            return None, None
        provider, api_key = found
        # Imported here: content_drafts imports this module's neighbours.
        from app.services.content_drafts import workspace_model_spend

        if (
            await workspace_model_spend(self.session, self.context.tenant_id)
            >= self.settings.content_draft_monthly_budget_micros
        ):
            return None, None
        model_name = (
            self.settings.agent_chat_anthropic_model
            if provider == "anthropic"
            else self.settings.agent_chat_openai_model
        )
        chat = self.chat_models[provider]
        permitted = visible_skills(self.context.role)
        reads = [skill for skill in permitted if skill.effect is SkillEffect.READ]
        actions = [skill for skill in permitted if skill.effect is not SkillEffect.READ]
        history = await self._history(session_id, before_sequence)
        conversation = [*history, {"role": "user", "content": body.strip()[:8000]}]

        spent = SkillAnswer("")
        spent.provider = provider
        try:
            plan = await chat.complete(
                api_key=api_key,
                model=model_name,
                system=PLAN_SYSTEM.format(read_skills=_describe(reads)),
                messages=conversation,
                schema=PLAN_SCHEMA,
                schema_name="skill_plan",
            )
            spent.cost_micros += plan.cost_micros
            spent.model = plan.model
            chosen = _pick(plan.answer.get("skills"), reads, CHAT_MAX_READS)
            question = str(plan.answer.get("clarifying_question") or "").strip()
            if not chosen and question:
                spent.body = question[:2000]
                return spent, None

            results: list[str] = []
            last_task: AgentTask | None = None
            for skill in chosen:
                last_task, result = await self._execute(skill, site, session_id, SkillAnswer(""), body)
                spent.evidence.extend(result.evidence)
                results.append(f"<skill_result key=\"{skill.key}\" name=\"{skill.name}\">\n{result.body}\n</skill_result>")

            reply = await chat.complete(
                api_key=api_key,
                model=model_name,
                system=ANSWER_SYSTEM.format(site=site.name, action_skills=_describe(actions + reads)),
                messages=[
                    *conversation,
                    {
                        "role": "user",
                        "content": "Skill results for the message above:\n\n"
                        + ("\n\n".join(results) or "(no skill was needed)"),
                    },
                ],
                schema=ANSWER_SCHEMA,
                schema_name="chat_answer",
            )
        except ChatModelError:
            # Any cost already incurred is lost from the ledger here; the
            # router answers instead so the person is never left with nothing.
            return None, None
        spent.cost_micros += reply.cost_micros
        spent.model = reply.model
        spent.body = str(reply.answer.get("answer") or "").strip()[:8000] or (
            "I could not put an answer together from what is stored for this site."
        )
        for skill in _pick(reply.answer.get("suggested_skills"), permitted, CHAT_MAX_SUGGESTIONS):
            spent.evidence.append(
                {"kind": "suggested_skill", "key": skill.key, "name": skill.name, "example": skill.example}
            )
        return spent, last_task

    async def _history(self, session_id: UUID, before_sequence: int) -> list[dict[str, str]]:
        rows = await self.session.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.tenant_id == self.context.tenant_id,
                AgentMessage.session_id == session_id,
                AgentMessage.sequence <= before_sequence,
            )
            .order_by(AgentMessage.sequence.desc())
            .limit(CHAT_HISTORY_MESSAGES)
        )
        turns = [
            {"role": "user" if row.role == "user" else "assistant", "content": row.body}
            for row in reversed(list(rows))
        ]
        # Both providers want the conversation to open with the person.
        while turns and turns[0]["role"] != "user":
            turns.pop(0)
        return turns

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
        self, skill: Skill, site: Site, session_id: UUID, answer: SkillAnswer, message: str
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
                answer, routine_run_id = await self._schedule(
                    skill, site, parse_cadence(message)
                )
                task.routine_run_id = routine_run_id
            elif skill.effect is SkillEffect.PROPOSE:
                answer = await self._propose(skill, site)
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

    async def _schedule(
        self, skill: Skill, site: Site, cadence: RequestedCadence | None
    ) -> tuple[SkillAnswer, UUID | None]:
        """Queue one run, or put the routine on a repeating schedule.

        A cadence is honoured only when the request actually named one, so
        asking to run something once never starts a recurring schedule.
        """
        assert skill.routine_kind is not None
        routines = RoutineService(self.session, self.context)

        if cadence is not None:
            routine = await routines.upsert(
                site.id,
                RoutineUpsert(
                    kind=skill.routine_kind,  # type: ignore[arg-type]
                    cadence=cadence.cadence,  # type: ignore[arg-type]
                    schedule_hour_utc=DEFAULT_SCHEDULE_HOUR_UTC,
                    schedule_isodow=cadence.isodow,
                    schedule_dom=cadence.dom,
                    enabled=True,
                ),
            )
            return (
                SkillAnswer(
                    f"Scheduled **{skill.name}** {cadence.cadence}. The next run is "
                    f"{routine.next_run_at:%Y-%m-%d %H:%M} UTC. It is skipped while the site "
                    "is unverified or frozen, and it cannot deploy anything. Ask me to stop "
                    f"it, or disable the `{skill.routine_kind}` routine, to end the schedule.",
                    [
                        {
                            "kind": "routine",
                            "id": str(routine.id),
                            "routine_kind": skill.routine_kind,
                            "cadence": routine.cadence,
                            "next_run_at": routine.next_run_at.isoformat(),
                        }
                    ],
                ),
                None,
            )

        routine = await self.session.scalar(
            select(Routine).where(
                Routine.tenant_id == self.context.tenant_id,
                Routine.site_id == site.id,
                Routine.kind == skill.routine_kind,
            )
        )
        if routine is None:
            # A one-off invocation creates the routine parked, never enabled:
            # asking for one run must not silently start a recurring schedule.
            # A routine that spends the workspace's AI key is parked weekly, so
            # enabling it later can never start a daily spend.
            paid = skill.routine_kind in PAID_ROUTINE_KINDS
            parked = Cadence.WEEKLY if paid else Cadence.DAILY
            schedule = RoutineSchedule(
                cadence=parked, hour_utc=DEFAULT_SCHEDULE_HOUR_UTC, isodow=1 if paid else None
            )
            routine = Routine(
                tenant_id=self.context.tenant_id,
                site_id=site.id,
                kind=skill.routine_kind,
                cadence=parked.value,
                schedule_isodow=1 if paid else None,
                schedule_hour_utc=DEFAULT_SCHEDULE_HOUR_UTC,
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
                f"Queued **{skill.name}** to run once. It runs in the worker under the same "
                "checks as a scheduled run: it is skipped if the site is unverified or "
                "frozen, and it cannot deploy anything. Say \"every day\" or \"every "
                "Monday\" if you want it on a repeating schedule.",
                [{"kind": "routine_run", "id": str(run.id), "routine_kind": skill.routine_kind}],
            ),
            run.id,
        )

    async def _propose(self, skill: Skill, site: Site) -> SkillAnswer:
        """Draft proposals for every open opportunity that has a safe repair.

        The connector is needed because a proposal's diff must be built against
        the file as it stands on the base branch; anything else would fail its
        own drift check at deploy time. Nothing here approves or deploys, and a
        proposal that already exists for an opportunity is left alone rather
        than duplicated.
        """
        if skill.key != "draft_fixes":
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="skill_not_implemented"
            )

        opportunities = await OpportunityService(self.session, self.context).list_top(
            site.id, PROPOSE_LIMIT, "open"
        )
        if not opportunities:
            return _no_evidence(
                "open opportunity", "Run a crawl and an audit first, then ask again."
            )

        drafted: list[str] = []
        skipped: dict[str, int] = {}
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(30.0)
        ) as client:
            credential = await credential_for_site(
                self.session, self.context, site.id, self.settings, client
            )
            adapter = GitHubDeploymentAdapter(client, credential.target, credential.token)
            drafts = ProposalDraftService(self.session, self.context, credential.path_template)
            proposals = ProposalService(self.session, self.context)
            for opportunity in opportunities:
                try:
                    site_id, command = await drafts.draft_from_opportunity(
                        opportunity.id, adapter.read_file
                    )
                except HTTPException as refusal:
                    # Every refusal is a reason a person would want to know, so
                    # they are counted and reported rather than swallowed.
                    reason = str(refusal.detail).split(":", 1)[0]
                    skipped[reason] = skipped.get(reason, 0) + 1
                    continue
                proposal = await proposals.create_proposal(site_id, command)
                drafted.append(f"`{proposal.target_path}` — {proposal.title}")

        if not drafted:
            summary = ", ".join(f"{count} {reason}" for reason, count in sorted(skipped.items()))
            return SkillAnswer(
                f"I drafted nothing. Every open opportunity was skipped: {summary}."
            )

        lines = "\n".join(f"- {row}" for row in drafted[:PROPOSE_REPORT_LIMIT])
        more = (
            f"\n- …and {len(drafted) - PROPOSE_REPORT_LIMIT} more"
            if len(drafted) > PROPOSE_REPORT_LIMIT
            else ""
        )
        skipped_note = ""
        if skipped:
            summary = ", ".join(f"{count} {reason}" for reason, count in sorted(skipped.items()))
            skipped_note = f"\n\nSkipped: {summary}."
        return SkillAnswer(
            f"I drafted **{len(drafted)}** proposals. None of them is approved and none "
            f"is deployed — each one needs a reviewer who is not its author.\n\n"
            f"{lines}{more}{skipped_note}"
        )

    async def _read(self, skill: Skill, site: Site) -> SkillAnswer:
        handlers = {
            "site_audit": self._read_site_audit,
            "search_performance": self._read_search_performance,
            "keyword_research": self._read_keywords,
            "content_briefs": self._read_briefs,
            "sitemap_review": self._read_sitemap,
            "competitor_pages": self._read_competitors,
            "ai_visibility": self._read_ai_visibility,
            "ai_citations": self._read_ai_citations,
            "weekly_report": self._read_weekly_report,
            "routines": self._read_routines,
        }
        handler = handlers.get(skill.key)
        if handler is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="skill_not_implemented"
            )
        return await handler(site)

    async def _read_search_performance(self, site: Site) -> SkillAnswer:
        # Search Console data lags by about two days.
        end = datetime.now(UTC).date() - timedelta(days=2)
        start = end - timedelta(days=27)
        prior_end = start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=27)
        service = SearchPerformanceService(self.session, self.context)
        current = await service.summarize(site.id, start, end)
        prior = await service.summarize(site.id, prior_start, prior_end)
        if current.rows == 0 and prior.rows == 0:
            return _no_evidence(
                "Search Console data", "Connect Search Console under Connections, then ask again."
            )

        def line(label: str, now: float, then: float, fmt: str) -> str:
            change = f" ({now - then:+{fmt}} vs prior)" if prior.rows else ""
            return f"{label}: {now:{fmt}}{change}"

        lines = [
            line("Clicks", current.clicks, prior.clicks, ",.0f"),
            line("Impressions", current.impressions, prior.impressions, ",.0f"),
        ]
        if current.position is not None:
            lines.append(
                f"Average position: {current.position:.1f}"
                + (f" (was {prior.position:.1f})" if prior.position is not None else "")
            )
        if current.ctr is not None:
            lines.append(f"Click-through rate: {current.ctr:.2%}")

        def window(lo: date, hi: date) -> Any:
            return (
                select(
                    SearchMetric.page_url,
                    func.sum(SearchMetric.impressions).label("impressions"),
                    func.sum(SearchMetric.clicks).label("clicks"),
                    (
                        func.sum(SearchMetric.position * SearchMetric.impressions)
                        / func.nullif(func.sum(SearchMetric.impressions), 0)
                    ).label("position"),
                )
                .where(
                    SearchMetric.tenant_id == self.context.tenant_id,
                    SearchMetric.site_id == site.id,
                    SearchMetric.metric_date >= lo,
                    SearchMetric.metric_date <= hi,
                )
                .group_by(SearchMetric.page_url)
            )

        now_rows = {row.page_url: row for row in (await self.session.execute(window(start, end))).all()}
        then_rows = {
            row.page_url: row for row in (await self.session.execute(window(prior_start, prior_end))).all()
        }
        top = sorted(now_rows.values(), key=lambda row: row.impressions or 0, reverse=True)[:SUMMARY_LIMIT]
        page_lines = [
            f"{row.page_url} — {row.impressions:,.0f} impressions, {row.clicks:,.0f} clicks"
            + (f", position {row.position:.1f}" if row.position is not None else "")
            for row in top
        ]
        movers = sorted(
            (
                ((now_rows[url].impressions if url in now_rows else 0) - (then_rows[url].impressions if url in then_rows else 0), url)
                for url in set(now_rows) | set(then_rows)
            ),
        )
        losers = [f"{url} ({delta:+,.0f} impressions)" for delta, url in movers[:3] if delta < 0]
        gainers = [f"{url} ({delta:+,.0f} impressions)" for delta, url in reversed(movers[-3:]) if delta > 0]

        text = (
            f"Search Console for {site.name}, {start} to {end}:\n\n{_bullet(lines)}"
            + (f"\n\nPages with the most impressions:\n\n{_bullet(page_lines)}" if page_lines else "")
            + (f"\n\nBiggest gains:\n\n{_bullet(gainers)}" if gainers else "")
            + (f"\n\nBiggest losses:\n\n{_bullet(losers)}" if losers else "")
        )
        if current.is_sparse:
            text += (
                "\n\nUnder 100 impressions in the window: too little data to read changes "
                "in position or click-through rate as real."
            )
        return SkillAnswer(text, [{"kind": "search_window", "start": str(start), "end": str(end)}])

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
        access = factors.get("ai_crawler_access")
        detail = access.get("detail", {}) if isinstance(access, dict) else {}
        blocked = [
            f"{row.get('token')} ({row.get('operator')}) is refused on "
            f"{(1 - float(row.get('allowed_share', 0))) * 100:.0f}% of pages"
            for row in detail.get("retrieval_blocked") or []
            if isinstance(row, dict)
        ]
        training = [
            str(row.get("token"))
            for row in detail.get("training_blocked") or []
            if isinstance(row, dict)
        ]
        llms = (latest.factors_json or {}).get("llms_txt")
        notes: list[str] = []
        if blocked:
            notes.append(
                "robots.txt keeps these answer-engine crawlers out, so they cannot "
                f"cite those pages:\n\n{_bullet(blocked)}"
            )
        if training:
            notes.append(
                f"Training crawlers refused (a choice that does not affect citations): "
                f"{', '.join(training)}."
            )
        if isinstance(llms, dict) and llms.get("status"):
            notes.append(f"llms.txt: {llms['status']} (reported, not scored).")
        trend = ""
        if len(snapshots) > 1:
            delta = latest.readiness_score - snapshots[1].readiness_score
            trend = f" ({delta:+.1f} since {snapshots[1].captured_on})"
        return SkillAnswer(
            f"Answer-engine readiness for {site.name} is "
            f"{latest.readiness_score:.0f}/100{trend}:\n\n{_bullet(lines)}\n\n"
            + "".join(f"{note}\n\n" for note in notes)
            + "This measures how well the site is positioned to be cited. It does not "
            "observe what any answer engine actually said; that needs a certified "
            "provider, which is not connected.",
            [{"kind": "ai_visibility_snapshot", "id": str(latest.id)}],
        )

    async def _read_ai_citations(self, site: Site) -> SkillAnswer:
        latest, observations, history = await AiCitationService(
            self.session, self.context
        ).report(site.id, history=2)
        if latest is None:
            return _no_evidence(
                "AI citation run",
                "Track a few questions under AI citations, store a Claude, OpenAI or Perplexity key, "
                "then run the AI citation routine.",
            )
        names = {"anthropic": "Claude", "openai": "ChatGPT", "perplexity": "Perplexity"}
        answered = [row for row in observations if row.status == "answered"]
        lines = []
        for row in answered:
            verdict = (
                f"cited (source #{row.own_citation_rank})" if row.site_cited
                else "named, not cited" if row.site_mentioned
                else "not cited"
            )
            lines.append(f"{names.get(row.provider, row.provider)} — {row.prompt}: {verdict}")
        failed = len(observations) - len(answered)
        trend = ""
        if len(history) > 1 and history[1].answers:
            trend = f" (previous run: {history[1].cited} of {history[1].answers})"
        return SkillAnswer(
            f"In the latest run, {site.name} was cited in {latest.cited} of "
            f"{latest.answers} answers and named in {latest.mentioned}{trend}:\n\n"
            f"{_bullet(lines)}\n\n"
            + (f"{failed} question(s) got no answer; the run's errors say why.\n\n" if failed else "")
            + "Observed through each provider's API with web search, which is close to "
            "but not the same as the consumer apps. Answers vary between runs, so read "
            "this as a sample, not a ranking.",
            [{"kind": "ai_citation_run", "id": str(latest.id)}],
        )

    async def _read_weekly_report(self, site: Site) -> SkillAnswer:
        report = await ReportService(self.session, self.context).latest(site.id, "weekly_digest")
        if report is None:
            return _no_evidence("weekly report", "Run the weekly report routine.")
        payload = report.payload_json or {}
        opportunities = payload.get("opportunities", {})
        search = payload.get("search", {})
        # `top` is capped for display, so its length would always read as the
        # cap. The per-category counts carry the real open total.
        open_total = sum(
            int(row.get("open_count", 0))
            for row in (opportunities.get("open_by_category") or [])
            if isinstance(row, dict)
        )
        lines = [
            (
                f"{opportunities.get('opened_in_period', 0)} findings opened, "
                f"{opportunities.get('resolved_in_period', 0)} resolved"
            ),
            f"{open_total} open findings in the review queue",
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


def _describe(skills: list[Skill]) -> str:
    return "\n".join(f"- {skill.key}: {skill.name}. {skill.description}" for skill in skills)


def _pick(keys: Any, allowed: list[Skill], limit: int) -> list[Skill]:
    """Keep only registry skills the actor may run, in order, without repeats."""
    if not isinstance(keys, list):
        return []
    by_key = {skill.key: skill for skill in allowed}
    picked: list[Skill] = []
    for key in keys:
        skill = by_key.get(key) if isinstance(key, str) else None
        if skill is not None and skill not in picked:
            picked.append(skill)
        if len(picked) == limit:
            break
    return picked
