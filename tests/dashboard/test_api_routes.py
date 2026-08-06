import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import db
from dashboard.config import DashboardConfig
from dashboard.routes.api import api_router

CONFIG = DashboardConfig(
    db_path="",  # overwritten per test
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="test-api-key",
)


@pytest.fixture
def client(tmp_path):
    config = DashboardConfig(**{**CONFIG.__dict__, "db_path": str(tmp_path / "test.db")})
    db.init_db(config.db_path)

    app = FastAPI()
    app.state.config = config
    app.include_router(api_router)
    return TestClient(app)


def test_get_servers_requires_api_key(client):
    response = client.get("/api/servers")
    assert response.status_code == 401


def test_get_servers_returns_registered_servers(client):
    config = client.app.state.config
    db.insert_server(config.db_path, "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log", "default", None)

    response = client.get("/api/servers", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["server_id"] == "server-a"
    assert body[0]["host"] == "10.0.1.10"


def test_post_errors_requires_api_key(client):
    response = client.post("/api/errors", json={})
    assert response.status_code == 401


def test_post_errors_stores_error(client):
    payload = {
        "server_id": "server-a",
        "timestamp": "2026-08-06T12:35:01+09:00",
        "log_level": "ERROR",
        "error_type": "java.lang.NullPointerException",
        "message": "boom",
        "stack_trace": "at ...",
        "raw_log": "raw",
        "ai_analysis": "원인: ...",
        "notified": True,
        "notified_at": "2026-08-06T12:35:05+09:00",
    }

    response = client.post("/api/errors", json=payload, headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 200
    config = client.app.state.config
    stored = db.query_errors(config.db_path)
    assert len(stored) == 1
    assert stored[0]["error_type"] == "java.lang.NullPointerException"
    assert stored[0]["notified"] == 1


def test_post_errors_missing_required_field_returns_422(client):
    response = client.post(
        "/api/errors",
        json={"server_id": "server-a"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 422
