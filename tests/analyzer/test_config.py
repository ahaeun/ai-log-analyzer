import pytest

from analyzer.config import load_config_from_env

REQUIRED_ENV = {
    "ANALYZER_API_KEY": "analyzer-key",
    "OPENAI_API_KEY": "sk-test",
    "REDIS_URL": "redis://localhost:6379/0",
    "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/x",
    "DASHBOARD_URL": "http://localhost:8000",
    "DASHBOARD_API_KEY": "dashboard-key",
}


@pytest.fixture
def set_required_env(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_load_config_from_env(set_required_env):
    config = load_config_from_env()

    assert config.api_key == "analyzer-key"
    assert config.openai_api_key == "sk-test"
    assert config.redis_url == "redis://localhost:6379/0"
    assert config.slack_webhook_url == "https://hooks.slack.com/services/x"
    assert config.dashboard_url == "http://localhost:8000"
    assert config.dashboard_api_key == "dashboard-key"


def test_missing_required_env_var_raises(set_required_env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        load_config_from_env()
