from fastapi.testclient import TestClient

from app.core.telemetry import format_traceparent_header, global_metrics
from app.main import app


def test_prometheus_metrics_endpoint() -> None:
    client = TestClient(app)
    global_metrics.total_proposals = 12
    global_metrics.total_deployed_proposals = 4

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "seo_autopilot_proposals_total 12" in response.text
    assert "seo_autopilot_proposals_deployed_total 4" in response.text


def test_w3c_traceparent_formatting() -> None:
    header = format_traceparent_header(trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7")
    assert header == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
