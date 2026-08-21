from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import require_tenant_context
from app.core.config import get_settings
from app.core.context import TenantContext

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_tenant_session(
    context: Annotated[TenantContext, Depends(require_tenant_context)],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(context.tenant_id)},
        )
        yield session


TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]


async def get_system_session() -> AsyncIterator[AsyncSession]:
    """Narrow unauthenticated callback session; service must derive tenant from one-time state."""
    async with session_factory() as session, session.begin():
        yield session


SystemSession = Annotated[AsyncSession, Depends(get_system_session)]
