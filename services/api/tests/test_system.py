from fastapi.testclient import TestClient

from app.main import app


def test_flags_default_closed():
    data = TestClient(app).get("/v1/system/readiness").json()["data"]
    assert not data["deployments_enabled"] and not data["autopilot_enabled"]


def test_site_api_fails_closed_without_trusted_identity() -> None:
    response = TestClient(app).get("/v1/sites")
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication_not_configured"
