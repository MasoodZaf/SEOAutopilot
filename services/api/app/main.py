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
from app.api.routes.tenants import credentials_router as tenant_credentials_router
from app.api.routes.tenants import router as tenants_router
from app.core.config import get_settings
from app.core.logging import configure_safe_access_logging
from app.db.session import relay_engine, tenant_scoped_session
from app.services.deployment_verification import run_verification_sweep
from app.services.proposal_drafting import run_drafting_sweep
from app.services.rollback_reconciliation import run_reconcile_sweep

settings = get_settings()
configure_safe_access_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Background work this process owns, started and stopped with it.

    Three sweeps, all polls, each holding its own Postgres advisory lock so
    running more than one API process sweeps once rather than N times.

    The reconciler asks what happened to a revert pull request: it is merged by
    a person, so nothing pushes that fact here and the choice is a poll or a
    webhook. A poll needs no public endpoint and no per-connector shared secret.

    The drafter turns opportunities into proposals as itself rather than as
    whoever clicked, which is what lets a single-member tenant approve a
    deterministic repair at all -- separation of duties is between the drafter
    and the approver, and here the drafter is a machine. It drafts only for
    sites whose mode says the platform may propose changes, and it deploys
    nothing.

    The verifier asks the live site whether a deployed change is actually
    there. Deploying opens a pull request and a person merges it, so the answer
    arrives at a time nothing here controls. It is also the beginning of the
    measurement chain: a measurement refuses without a verification, and until
    this existed nothing ever created one, so no outcome could be computed for
    any change on any site however long anybody waited.

    Each is started only if configured, and each is cancelled and awaited on
    shutdown so a sweep in flight does not outlive the process that owns it.
    """
    tasks: list[asyncio.Task[None]] = []
    client: httpx.AsyncClient | None = None
    if (
        settings.rollback_reconcile_enabled
        or settings.proposal_drafting_enabled
        or settings.deployment_verification_enabled
    ):
        client = httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15.0))
    if settings.rollback_reconcile_enabled and client is not None:
        tasks.append(
            asyncio.create_task(
                run_reconcile_sweep(
                    relay_engine,
                    tenant_scoped_session,
                    settings,
                    client,
                    interval_seconds=settings.rollback_reconcile_interval_seconds,
                )
            )
        )
    if settings.proposal_drafting_enabled and client is not None:
        tasks.append(
            asyncio.create_task(
                run_drafting_sweep(
                    relay_engine,
                    tenant_scoped_session,
                    settings,
                    client,
                    interval_seconds=settings.proposal_drafting_interval_seconds,
                    limit=settings.proposal_drafting_batch,
                )
            )
        )
    if settings.deployment_verification_enabled and client is not None:
        tasks.append(
            asyncio.create_task(
                run_verification_sweep(
                    relay_engine,
                    tenant_scoped_session,
                    settings,
                    client,
                    interval_seconds=settings.deployment_verification_interval_seconds,
                    limit=settings.deployment_verification_batch,
                )
            )
        )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if client is not None:
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
app.include_router(tenants_router)
app.include_router(tenant_credentials_router)
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

