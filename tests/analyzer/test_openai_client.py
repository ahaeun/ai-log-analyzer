from unittest.mock import MagicMock, patch

from watcher.models import ErrorEvent

from analyzer.config import AnalyzerConfig
from analyzer.openai_client import analyze_error

CONFIG = AnalyzerConfig(
    api_key="k", openai_api_key="sk-test", redis_url="redis://localhost:6379/0",
    slack_webhook_url="https://hooks.slack.com/x", dashboard_url="http://localhost:8000",
    dashboard_api_key="dk",
)

EVENT = ErrorEvent(
    server_id="server-a", timestamp="2026-08-06T10:00:00+09:00", log_level="ERROR",
    error_type="java.lang.NullPointerException", message="boom", stack_trace="at ...",
    raw_log="raw",
)


def test_analyze_error_returns_analysis_text():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="원인: ... / 해결방향: ..."))
    ]

    with patch("analyzer.openai_client.OpenAI", return_value=fake_client):
        result = analyze_error(EVENT, CONFIG)

    assert result == "원인: ... / 해결방향: ..."


def test_analyze_error_returns_none_on_failure():
    with patch("analyzer.openai_client.OpenAI", side_effect=RuntimeError("boom")):
        result = analyze_error(EVENT, CONFIG)

    assert result is None


def test_analyze_error_passes_event_details_in_prompt():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="ok"))
    ]

    with patch("analyzer.openai_client.OpenAI", return_value=fake_client):
        analyze_error(EVENT, CONFIG)

    _, kwargs = fake_client.chat.completions.create.call_args
    user_message = kwargs["messages"][1]["content"]
    assert "java.lang.NullPointerException" in user_message
    assert "boom" in user_message
