from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    AgentMessageCollection,
    AgentMessageCreate,
    AgentMessageRead,
    AgentSessionCollection,
    AgentSessionCreate,
    AgentSessionEnvelope,
    AgentSessionRead,
    AgentTaskCollection,
    AgentTaskRead,
    AgentTurnEnvelope,
    SkillCollection,
    SkillRead,
)
from app.core.auth import TenantContextDependency
from app.core.config import Settings, get_settings
from app.db.session import TenantSession
from app.domain.skills import SkillEffect
from app.services.agent import AgentService, visible_skills
from app.services.connector_secrets import decode_encryption_key

router = APIRouter(prefix="/v1", tags=["agent"])


def agent_service(
    settings: Settings, session: TenantSession, context: TenantContextDependency
) -> AgentService:
    key: bytes | None = None
    if (
        settings.connector_secret_backend == "database_envelope"
        and settings.connector_secret_encryption_key
    ):
        key = decode_encryption_key(settings.connector_secret_encryption_key.get_secret_value())
    return AgentService(session, context, keyword_encryption_key=key)


@router.get("/skills", response_model=SkillCollection)
async def list_skills(context: TenantContextDependency) -> SkillCollection:
    """Only skills this actor's role may invoke. The list is the whole surface."""
    skills = visible_skills(context.role)
    return SkillCollection(
        data=[
            SkillRead(
                key=skill.key,
                name=skill.name,
                description=skill.description,
                effect=skill.effect.value,
                example=skill.example,
                schedules_work=skill.effect is SkillEffect.SCHEDULE,
            )
            for skill in skills
        ],
        meta={"trace_id": context.trace_id, "count": len(skills)},
    )


@router.get("/sites/{site_id}/agent-sessions", response_model=AgentSessionCollection)
async def list_agent_sessions(
    site_id: UUID, context: TenantContextDependency, session: TenantSession
) -> AgentSessionCollection:
    sessions = await agent_service(get_settings(), session, context).list_sessions(site_id)
    if sessions is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return AgentSessionCollection(
        data=[AgentSessionRead.model_validate(item) for item in sessions],
        meta={"trace_id": context.trace_id, "count": len(sessions)},
    )


@router.post(
    "/sites/{site_id}/agent-sessions",
    response_model=AgentSessionEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_session(
    site_id: UUID,
    command: AgentSessionCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> AgentSessionEnvelope:
    record = await agent_service(get_settings(), session, context).create_session(
        site_id, command.title
    )
    return AgentSessionEnvelope(
        data=AgentSessionRead.model_validate(record), meta={"trace_id": context.trace_id}
    )


@router.get("/agent-sessions/{session_id}/messages", response_model=AgentMessageCollection)
async def list_agent_messages(
    session_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=100, ge=1, le=200),
) -> AgentMessageCollection:
    service = agent_service(get_settings(), session, context)
    if await service.get_session(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent_session_not_found"
        )
    messages = await service.list_messages(session_id, limit)
    return AgentMessageCollection(
        data=[AgentMessageRead.model_validate(item) for item in messages],
        meta={"trace_id": context.trace_id, "count": len(messages)},
    )


@router.post(
    "/agent-sessions/{session_id}/messages",
    response_model=AgentTurnEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def post_agent_message(
    session_id: UUID,
    command: AgentMessageCreate,
    context: TenantContextDependency,
    session: TenantSession,
) -> AgentTurnEnvelope:
    """Route one message to at most one skill and answer from stored evidence.

    A message is never tool authority: the router selects only from the fixed
    skill registry, and every skill re-checks the actor's role.
    """
    request, reply, task = await agent_service(
        get_settings(), session, context
    ).post_message(session_id, command.body, command.skill_key)
    return AgentTurnEnvelope(
        data=AgentMessageRead.model_validate(reply),
        request=AgentMessageRead.model_validate(request),
        task=AgentTaskRead.model_validate(task) if task else None,
        meta={"trace_id": context.trace_id},
    )


@router.get("/sites/{site_id}/agent-tasks", response_model=AgentTaskCollection)
async def list_agent_tasks(
    site_id: UUID,
    context: TenantContextDependency,
    session: TenantSession,
    limit: int = Query(default=25, ge=1, le=100),
) -> AgentTaskCollection:
    tasks = await agent_service(get_settings(), session, context).list_tasks(site_id, limit)
    if tasks is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site_not_found")
    return AgentTaskCollection(
        data=[AgentTaskRead.model_validate(item) for item in tasks],
        meta={"trace_id": context.trace_id, "count": len(tasks)},
    )
