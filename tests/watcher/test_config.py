import pytest
from watcher.config import load_server_config


def _write_yaml(tmp_path, content):
    path = tmp_path / "server.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_load_default_format_config(tmp_path):
    path = _write_yaml(tmp_path, """
server_id: server-a
log_path: /var/log/app/application.log
format: default
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    config = load_server_config(path)

    assert config.server_id == "server-a"
    assert config.log_path == "/var/log/app/application.log"
    assert config.format == "default"
    assert config.custom_pattern is None
    assert config.central_endpoint == "https://collector.example.com/api/errors"
    assert config.api_key_env == "WATCHER_API_KEY"


def test_load_custom_format_config(tmp_path):
    path = _write_yaml(tmp_path, r"""
server_id: server-b
log_path: /var/log/app/custom.log
format: custom
custom_pattern: '^(?P<timestamp>\S+)\s+(?P<level>\w+)\s+(?P<logger>\S+)\s+(?P<message>.*)$'
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    config = load_server_config(path)

    assert config.format == "custom"
    assert config.custom_pattern is not None


def test_missing_required_field_raises(tmp_path):
    path = _write_yaml(tmp_path, """
server_id: server-a
log_path: /var/log/app/application.log
format: default
""")

    with pytest.raises(ValueError, match="Missing required config fields"):
        load_server_config(path)


def test_custom_format_without_pattern_raises(tmp_path):
    path = _write_yaml(tmp_path, """
server_id: server-a
log_path: /var/log/app/application.log
format: custom
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    with pytest.raises(ValueError, match="custom_pattern is required"):
        load_server_config(path)


def test_custom_format_missing_named_groups_raises(tmp_path):
    path = _write_yaml(tmp_path, r"""
server_id: server-a
log_path: /var/log/app/application.log
format: custom
custom_pattern: '^(?P<message>.*)$'
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    with pytest.raises(ValueError, match="custom_pattern must define named groups"):
        load_server_config(path)


def test_custom_format_invalid_regex_raises_value_error(tmp_path):
    path = _write_yaml(tmp_path, r"""
server_id: server-a
log_path: /var/log/app/application.log
format: custom
custom_pattern: '(?P<timestamp>['
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    with pytest.raises(ValueError, match="invalid custom_pattern regex"):
        load_server_config(path)
