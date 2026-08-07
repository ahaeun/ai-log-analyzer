from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from analyzer.config import AnalyzerConfig
from analyzer.main import create_app

CONFIG = AnalyzerConfig(
    api_key="test-key", openai_api_key="sk", redis_url="redis://localhost:6379/0",
    slack_webhook_url="https://hooks.slack.com/x", dashboard_url="http://localhost:8000",
    dashboard_api_key="dk",
)

PAYLOAD = {
    "server_id": "server-a",
    "timestamp": "2026-08-06T10:00:00+09:00",
    "log_level": "ERROR",
    "error_type": "java.lang.NullPointerException",
    "message": "boom",
    "stack_trace": "at ...",
    "raw_log": "raw",
}


@pytest.fixture
def client():
    app = create_app(CONFIG)
    return TestClient(app)


def test_receive_error_requires_api_key(client):
    response = client.post("/api/errors", json=PAYLOAD)
    assert response.status_code == 401


def test_receive_error_rejects_wrong_api_key_even_with_invalid_body(client):
    response = client.post("/api/errors", json={}, headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_receive_error_missing_field_returns_422(client):
    bad_payload = {k: v for k, v in PAYLOAD.items() if k != "message"}
    response = client.post("/api/errors", json=bad_payload, headers={"X-API-Key": "test-key"})
    assert response.status_code == 422


def test_receive_error_accepts_and_schedules_processing(client):
    with patch("analyzer.main.process_error") as mock_process:
        response = client.post("/api/errors", json=PAYLOAD, headers={"X-API-Key": "test-key"})

    assert response.status_code == 202
    mock_process.assert_called_once()
    args, _ = mock_process.call_args
    event = args[0]
    assert event.server_id == "server-a"
    assert event.error_type == "java.lang.NullPointerException"
    assert args[1] is CONFIG
