from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.telemetry import global_metrics

router = APIRouter(tags=["telemetry"])


@router.get("/metrics", response_class=PlainTextResponse)
async def get_prometheus_metrics() -> str:
    """Exposes real-time Prometheus scrape metrics."""
    return global_metrics.generate_prometheus_output()
