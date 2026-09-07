"""Prometheus scrape output, which is not public information.

The endpoint sits under the same `/api/*` prefix the reverse proxy publishes, so
until now anybody on the internet could read this deployment's operational
posture -- proposal and deployment volumes over time, and whether the emergency
kill switch is on. No tenant data, but nothing that needed to be readable by
strangers either, and nothing external scrapes it.

It is gated in the application rather than at the edge on purpose. An edge rule
is one config value away from silently not applying, which is exactly how the
control plane came to be unauthenticated for days.
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.telemetry import global_metrics

router = APIRouter(tags=["telemetry"])
scrape_bearer = HTTPBearer(auto_error=False)


def _permitted(
    settings: Settings, credentials: HTTPAuthorizationCredentials | None
) -> bool:
    """A scraper presents the token, or there is nothing to read.

    Deliberately not "development is exempt". The live deployment runs with
    `APP_ENV=development` until the identity cutover, so keying this on the
    environment would leave the endpoint public in the one place that matters,
    which is how this was exposed to begin with. No token configured means no
    metrics served, anywhere.
    """
    if settings.metrics_scrape_token is None:
        return False
    if credentials is None or credentials.scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(
        credentials.credentials, settings.metrics_scrape_token.get_secret_value()
    )


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def get_prometheus_metrics(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(scrape_bearer)],
) -> str:
    """Exposes real-time Prometheus scrape metrics to a scraper that says who it is."""
    if not _permitted(settings, credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="metrics_scrape_unauthorized"
        )
    return global_metrics.generate_prometheus_output()
