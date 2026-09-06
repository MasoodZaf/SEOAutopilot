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
    """One transaction per request, with the tenant GUC scoped to it.

    `set_config(..., true)` is transaction-local, which is what keeps a pooled
    connection from carrying one tenant's scope into another request. It also
    means a service must never commit mid-request: committing would end the
    transaction and drop the GUC, leaving every later statement unscoped and
    blocked by row-level security. Services stage writes with `flush()` and this
    context manager commits once, on a clean exit, or rolls the whole request
    back.
    """
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(context.tenant_id)},
        )
        yield session


TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]


async def get_system_session() -> AsyncIterator[AsyncSession]:
    """Narrow unauthenticated callback session; service must derive tenant from one-time state.

    The OAuth callback carries no tenant, because the tenant is what the state
    row establishes. Rather than run unscoped, this sets `app.oauth_callback`,
    which a single SELECT-only policy on `connector_oauth_state` accepts. Every
    other table stays closed, and the service sets `app.tenant_id` from the row
    it resolves before it reads or writes anything else.
    """
    async with session_factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.oauth_callback', 'on', true)"))
        yield session


SystemSession = Annotated[AsyncSession, Depends(get_system_session)]
