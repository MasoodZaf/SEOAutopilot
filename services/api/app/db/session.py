from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import require_tenant_context
from app.core.config import get_settings
from app.core.context import TenantContext

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)

# The sweep identity. It exists only so that "find unfinished work in tenants
# nobody is currently acting for" has somewhere to run that is not the
# application role, which by design can see nothing without a tenant scope, and
# not a superuser. When it is unset the application role is used instead, where
# the sweep finds nothing at all -- which is the safe way to be misconfigured.
relay_engine = create_async_engine(
    settings.relay_database_url or settings.database_url, pool_pre_ping=True, pool_size=2
)


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


@asynccontextmanager
async def authenticating_session() -> AsyncIterator[AsyncSession]:
    """The window in which a token becomes a tenant, and nothing wider.

    Authentication has the same shape of problem the OAuth callback has: the
    tenant is what this lookup establishes, so the lookup cannot be scoped by
    it. It gets the same kind of narrow exception -- `app.authenticating`, which
    only the policies on `app_user`, `tenant_membership` and `tenant_invitation`
    accept. Every other table stays closed to this session.

    It is a context manager rather than a request dependency on purpose. A
    dependency would hold a second pooled connection for the whole request
    alongside the tenant-scoped one; this returns it as soon as the membership
    is resolved, before any route runs.
    """
    async with session_factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.authenticating', 'on', true)"))
        yield session


@asynccontextmanager
async def tenant_scoped_session(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """A tenant-scoped transaction outside a request.

    Background work still has to declare whose data it is touching. This is the
    same shape `get_tenant_session` gives a request -- one transaction, the GUC
    set inside it, one commit on a clean exit -- for callers that have no
    request to hang it on.
    """
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session
