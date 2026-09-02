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

settings = get_settings()
configure_safe_access_logging()
app = FastAPI(title="SEO Autopilot API", version="0.1.0", redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_base_url.rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match"],
)
app.include_router(system_router)
app.include_router(metrics_router)
if settings.app_env == "development":
    app.include_router(local_pilot_router)
app.include_router(connectors_router)
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

