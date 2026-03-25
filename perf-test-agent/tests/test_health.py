from fastapi.testclient import TestClient

from src.api.main import app


def test_health_endpoint_reports_healthy() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "active_runs": 0,
        "hitl_pending": 0,
        "ws_connections": 0,
    }
