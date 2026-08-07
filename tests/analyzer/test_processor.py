from unittest.mock import patch

from watcher.models import ErrorEvent

from analyzer.config import AnalyzerConfig
from analyzer.processor import process_error

CONFIG = AnalyzerConfig(
    api_key="k", openai_api_key="sk", redis_url="redis://localhost:6379/0",
    slack_webhook_url="https://hooks.slack.com/x", dashboard_url="http://localhost:8000",
    dashboard_api_key="dk",
)

EVENT = ErrorEvent(
    server_id="server-a", timestamp="2026-08-06T10:00:00+09:00", log_level="ERROR",
    error_type="java.lang.NullPointerException", message="boom", stack_trace="at ...",
    raw_log="raw",
)


def test_process_error_sends_slack_and_stores_when_not_duplicate():
    with patch("analyzer.processor.dedup.is_duplicate", return_value=False), \
         patch("analyzer.processor.openai_client.analyze_error", return_value="분석결과"), \
         patch("analyzer.processor.slack_client.send_notification") as mock_slack, \
         patch("analyzer.processor.dashboard_client.store_error") as mock_store:
        process_error(EVENT, CONFIG)

    mock_slack.assert_called_once_with(CONFIG.slack_webhook_url, EVENT, "분석결과")
    args, _ = mock_store.call_args
    assert args[0] is CONFIG
    assert args[1] is EVENT
    assert args[2] == "분석결과"
    assert args[3] is True
    assert args[4] is not None


def test_process_error_skips_slack_but_still_stores_when_duplicate():
    with patch("analyzer.processor.dedup.is_duplicate", return_value=True), \
         patch("analyzer.processor.openai_client.analyze_error", return_value="분석결과"), \
         patch("analyzer.processor.slack_client.send_notification") as mock_slack, \
         patch("analyzer.processor.dashboard_client.store_error") as mock_store:
        process_error(EVENT, CONFIG)

    mock_slack.assert_not_called()
    args, _ = mock_store.call_args
    assert args[3] is False
    assert args[4] is None


def test_process_error_never_raises_and_logs_when_a_collaborator_fails():
    # A collaborator failing partway through (here: slack_client, which runs
    # before dashboard_client.store_error in the non-duplicate branch) must
    # not propagate out of process_error -- the BackgroundTasks callback has
    # no way to report a raised exception back to watcher, so the event
    # would otherwise vanish silently. dashboard_client.store_error is not
    # expected to run in this scenario since the exception happens before
    # that point in the non-duplicate branch; the guarantee under test is
    # only that process_error itself never raises, and that the failure is
    # logged.
    with patch("analyzer.processor.dedup.is_duplicate", return_value=False), \
         patch("analyzer.processor.openai_client.analyze_error", return_value="분석결과"), \
         patch(
             "analyzer.processor.slack_client.send_notification",
             side_effect=RuntimeError("boom"),
         ), \
         patch("analyzer.processor.dashboard_client.store_error") as mock_store, \
         patch("analyzer.processor.logger.exception") as mock_log_exception:
        process_error(EVENT, CONFIG)  # must not raise

    mock_log_exception.assert_called_once()


def test_process_error_continues_when_openai_analysis_fails():
    with patch("analyzer.processor.dedup.is_duplicate", return_value=False), \
         patch("analyzer.processor.openai_client.analyze_error", return_value=None), \
         patch("analyzer.processor.slack_client.send_notification") as mock_slack, \
         patch("analyzer.processor.dashboard_client.store_error") as mock_store:
        process_error(EVENT, CONFIG)

    mock_slack.assert_called_once_with(CONFIG.slack_webhook_url, EVENT, None)
    args, _ = mock_store.call_args
    assert args[2] is None
