from unittest.mock import patch

import requests
from watcher.models import ErrorEvent

from analyzer.config import AnalyzerConfig
from analyzer.dashboard_client import store_error

CONFIG = AnalyzerConfig(
    api_key="k", openai_api_key="sk", redis_url="redis://localhost:6379/0",
    slack_webhook_url="https://hooks.slack.com/x", dashboard_url="http://localhost:8000",
    dashboard_api_key="dashboard-key",
)

EVENT = ErrorEvent(
    server_id="server-a", timestamp="2026-08-06T10:00:00+09:00", log_level="ERROR",
    error_type="java.lang.NullPointerException", message="boom", stack_trace="at ...",
    raw_log="raw",
)


def test_store_error_posts_full_payload_with_api_key_header():
    with patch("analyzer.dashboard_client.requests.post") as mock_post:
        store_error(CONFIG, EVENT, "원인: ...", True, "2026-08-06T10:00:05+09:00")

    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:8000/api/errors"
    assert kwargs["headers"]["X-API-Key"] == "dashboard-key"
    payload = kwargs["json"]
    assert payload["server_id"] == "server-a"
    assert payload["ai_analysis"] == "원인: ..."
    assert payload["notified"] is True
    assert payload["notified_at"] == "2026-08-06T10:00:05+09:00"


def test_store_error_does_not_raise_on_network_failure():
    with patch("analyzer.dashboard_client.requests.post", side_effect=requests.ConnectionError):
        store_error(CONFIG, EVENT, None, False, None)
