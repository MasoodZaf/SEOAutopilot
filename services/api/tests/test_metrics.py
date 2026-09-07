"""Who may read this deployment's operational posture.

The endpoint lives under the `/api/*` prefix the reverse proxy publishes, so it
was readable by anybody on the internet: proposal and deployment volumes over
time, and whether the emergency kill switch is on. No tenant data, and nothing
that needed to be public.

The rule is fail-closed on purpose rather than keyed on the environment. The
live deployment runs `APP_ENV=development` until the identity cutover, so an
environment check would have exempted the one place that mattered.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.telemetry import format_traceparent_header, global_metrics
from app.main import app

TOKEN = "metrics-scrape-token-long-enough-32"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "development",
        "cursor_signing_key": "c" * 32,
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def configured():
    def apply(**overrides: Any) -> TestClient:
        app.dependency_overrides[get_settings] = lambda: settings(**overrides)
        return TestClient(app)

    yield apply
    app.dependency_overrides.clear()


def test_a_scraper_with_the_token_reads_the_metrics(configured) -> None:
    global_metrics.total_proposals = 12
    global_metrics.total_deployed_proposals = 4

    response = configured(metrics_scrape_token=SecretStr(TOKEN)).get(
        "/metrics", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "seo_autopilot_proposals_total 12" in response.text
    assert "seo_autopilot_proposals_deployed_total 4" in response.text


def test_without_a_token_configured_nothing_is_served_anywhere(configured) -> None:
    """Including in development, because production runs as development here."""
    response = configured().get("/metrics")
    assert response.status_code == 401
    assert response.json()["detail"] == "metrics_scrape_unauthorized"


def test_a_wrong_or_missing_credential_reads_nothing(configured) -> None:
    client = configured(metrics_scrape_token=SecretStr(TOKEN))
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    )


def test_a_short_scrape_token_is_refused_at_startup() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="METRICS_SCRAPE_TOKEN"):
        settings(metrics_scrape_token=SecretStr("too-short"))


def test_w3c_traceparent_formatting() -> None:
    header = format_traceparent_header(
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7"
    )
    assert header == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
