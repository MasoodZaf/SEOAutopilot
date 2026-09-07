import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.agent import router as agent_router
from app.api.routes.briefs import router as briefs_router
from app.api.routes.competitors import router as competitors_router
from app.api.routes.connectors import router as connectors_router
from app.api.routes.governance import router as governance_router
from app.api.routes.keywords import router as keywords_router
from app.api.routes.local_pilot import router as local_pilot_router
from app.api.routes.measurements import router as measurements_router
from app.api.routes.members import router as members_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.opportunities import calibration_router
from app.api.routes.opportunities import router as opportunities_router
from app.api.routes.pages import router as pages_router
from app.api.routes.proposals import router as proposals_router
from app.api.routes.routines import router as routines_router
from app.api.routes.sites import router as sites_router
from app.api.routes.system import router as system_router
from app.core.config import get_settings
from app.core.logging import configure_safe_access_logging
from app.db.session import relay_engine, tenant_scoped_session
from app.services.rollback_reconciliation import run_reconcile_sweep

settings = get_settings()
configure_safe_access_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Background work this process owns, started and stopped with it.

    Only the rollback reconciler lives here. A revert pull request is merged by
    a person, so nothing pushes that fact to this service and the choice is a
    poll or a webhook; a poll needs no public endpoint and no per-connector
    shared secret. It holds a Postgres advisory lock, so running more than one
    API process sweeps once rather than N times.
    """
    if not settings.rollback_reconcile_enabled:
        yield
        return
    client = httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15.0))
    sweep = asyncio.create_task(
        run_reconcile_sweep(
            relay_engine,
            tenant_scoped_session,
            settings,
            client,
            interval_seconds=settings.rollback_reconcile_interval_seconds,
        )
    )
    try:
        yield
    finally:
        sweep.cancel()
        try:
            await sweep
        except asyncio.CancelledError:
            pass
        await client.aclose()


# The schema and the interactive docs are a complete map of every endpoint,
# parameter and payload, including the governance and deployment routes: useful
# to hand a developer, unnecessary to hand a stranger. Off unless asked for, and
# by its own flag rather than by `app_env`, which on the live host still says
# "development".
app = FastAPI(
    title="SEO Autopilot API",
    version="0.1.0",
    docs_url="/docs" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_base_url.rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match", "X-Tenant-Id"],
)
app.include_router(system_router)
app.include_router(metrics_router)
if settings.app_env == "development":
    app.include_router(local_pilot_router)
app.include_router(connectors_router)
app.include_router(members_router)
app.include_router(sites_router)
app.include_router(pages_router)
app.include_router(opportunities_router)
app.include_router(calibration_router)
app.include_router(proposals_router)
app.include_router(measurements_router)
app.include_router(governance_router)
app.include_router(routines_router)
app.include_router(keywords_router)
app.include_router(briefs_router)
app.include_router(competitors_router)
app.include_router(agent_router)

