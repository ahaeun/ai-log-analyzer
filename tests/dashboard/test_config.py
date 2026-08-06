import os

import pytest

from dashboard.config import load_config_from_env

REQUIRED_ENV = {
    "SLACK_CLIENT_ID": "client-id",
    "SLACK_CLIENT_SECRET": "client-secret",
    "SLACK_TEAM_ID": "T12345",
    "DASHBOARD_ALLOWED_EMAILS": "a@example.com, b@example.com",
    "DASHBOARD_SESSION_SECRET": "session-secret",
    "DASHBOARD_API_KEY": "api-key",
}


@pytest.fixture
def set_required_env(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_load_config_from_env(set_required_env, monkeypatch):
    monkeypatch.setenv("DASHBOARD_DB_PATH", "/tmp/dashboard-test.db")

    config = load_config_from_env()

    assert config.db_path == "/tmp/dashboard-test.db"
    assert config.slack_client_id == "client-id"
    assert config.slack_client_secret == "client-secret"
    assert config.slack_team_id == "T12345"
    assert config.allowed_emails == ["a@example.com", "b@example.com"]
    assert config.session_secret == "session-secret"
    assert config.api_key == "api-key"


def test_db_path_defaults_when_not_set(set_required_env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_DB_PATH", raising=False)

    config = load_config_from_env()

    assert config.db_path == "dashboard/data.db"


def test_missing_required_env_var_raises(set_required_env, monkeypatch):
    monkeypatch.delenv("SLACK_CLIENT_ID", raising=False)

    with pytest.raises(ValueError, match="SLACK_CLIENT_ID"):
        load_config_from_env()
