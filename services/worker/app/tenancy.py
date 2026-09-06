"""Tenant scoping for worker database connections.

The API sets `app.tenant_id` on every request so row-level security can enforce
isolation. The worker did not, which was invisible while it connected as a
superuser and every policy was inert. Migration 0027 changed that, so each unit
of work here has to declare whose data it is touching.

Scope is set at session level rather than transaction level because a unit of
work spans several statements and, for the pagespeed and routine consumers, an
outbound HTTP call. Holding a transaction open across a network call would keep
row locks for its duration. The scope is cleared in a `finally` rather than
relying on asyncpg resetting session state when a connection returns to the pool
-- it does, and this does not depend on it.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

SET_SCOPE = "SELECT set_config('app.tenant_id',$1,false)"
CLEAR_SCOPE = "SELECT set_config('app.tenant_id','',false)"


@asynccontextmanager
async def tenant_scope(connection: Any, tenant_id: UUID) -> AsyncIterator[Any]:
    """Scope every statement on this connection to one tenant."""
    await connection.execute(SET_SCOPE, str(tenant_id))
    try:
        yield connection
    finally:
        try:
            await connection.execute(CLEAR_SCOPE)
        except Exception:  # noqa: BLE001 - see below
            # If the connection died mid-job, raising here would replace the
            # real failure with a confusing one. asyncpg resets session state
            # when the connection returns to the pool, so the scope does not
            # survive either way; the explicit clear is for the healthy case.
            logger.warning("could not clear tenant scope; relying on pool reset")
