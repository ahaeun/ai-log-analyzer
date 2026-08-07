from unittest.mock import patch

import requests
from watcher.models import ErrorEvent

from analyzer.slack_client import send_notification

EVENT = ErrorEvent(
    server_id="server-a", timestamp="2026-08-06T10:00:00+09:00", log_level="ERROR",
    error_type="java.lang.NullPointerException", message="boom", stack_trace="at ...",
    raw_log="raw",
)


def test_send_notification_posts_to_webhook_with_event_details():
    with patch("analyzer.slack_client.requests.post") as mock_post:
        send_notification("https://hooks.slack.com/x", EVENT, "원인: ...")

    args, kwargs = mock_post.call_args
    assert args[0] == "https://hooks.slack.com/x"
    text = kwargs["json"]["text"]
    assert "server-a" in text
    assert "java.lang.NullPointerException" in text
    assert "원인: ..." in text


def test_send_notification_omits_analysis_section_when_none():
    with patch("analyzer.slack_client.requests.post") as mock_post:
        send_notification("https://hooks.slack.com/x", EVENT, None)

    _, kwargs = mock_post.call_args
    assert "분석" not in kwargs["json"]["text"]


def test_send_notification_does_not_raise_on_network_failure():
    with patch("analyzer.slack_client.requests.post", side_effect=requests.ConnectionError):
        send_notification("https://hooks.slack.com/x", EVENT, "x")


def test_send_notification_does_not_raise_on_non_request_exception():
    # requests.post may raise something other than RequestException (e.g. a
    # bug, or a lower-level error); this must not propagate either, since a
    # propagated exception here would skip dashboard_client.store_error entirely.
    with patch("analyzer.slack_client.requests.post", side_effect=RuntimeError("unexpected")):
        send_notification("https://hooks.slack.com/x", EVENT, "x")
