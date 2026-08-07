import pytest

from watcher.config import load_watcher_config


def _write_yaml(tmp_path, content):
    path = tmp_path / "watcher.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_load_minimal_config_uses_defaults(tmp_path):
    path = _write_yaml(tmp_path, """
registry_url: http://dashboard.internal/api/servers
analyzer_endpoint: https://analyzer.internal/api/errors
api_key_env: WATCHER_API_KEY
registry_api_key_env: WATCHER_REGISTRY_API_KEY
""")

    config = load_watcher_config(path)

    assert config.registry_url == "http://dashboard.internal/api/servers"
    assert config.analyzer_endpoint == "https://analyzer.internal/api/errors"
    assert config.api_key_env == "WATCHER_API_KEY"
    assert config.registry_api_key_env == "WATCHER_REGISTRY_API_KEY"
    assert config.queue_dir == "watcher/.queue"
    assert config.registry_poll_interval == 30
    assert config.log_poll_interval == 15


def test_load_config_with_overrides(tmp_path):
    path = _write_yaml(tmp_path, """
registry_url: http://dashboard.internal/api/servers
analyzer_endpoint: https://analyzer.internal/api/errors
api_key_env: WATCHER_API_KEY
registry_api_key_env: WATCHER_REGISTRY_API_KEY
queue_dir: /var/lib/watcher/queue
registry_poll_interval: 60
log_poll_interval: 5
""")

    config = load_watcher_config(path)

    assert config.queue_dir == "/var/lib/watcher/queue"
    assert config.registry_poll_interval == 60
    assert config.log_poll_interval == 5


def test_missing_required_field_raises(tmp_path):
    path = _write_yaml(tmp_path, """
registry_url: http://dashboard.internal/api/servers
""")

    with pytest.raises(ValueError, match="Missing required config fields"):
        load_watcher_config(path)


def test_missing_registry_api_key_env_raises(tmp_path):
    path = _write_yaml(tmp_path, """
registry_url: http://dashboard.internal/api/servers
analyzer_endpoint: https://analyzer.internal/api/errors
api_key_env: WATCHER_API_KEY
""")

    with pytest.raises(ValueError, match="Missing required config fields"):
        load_watcher_config(path)
