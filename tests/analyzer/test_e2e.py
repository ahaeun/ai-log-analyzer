"""End-to-end test for the real receive -> 202 -> BackgroundTasks -> process_error chain.

Only the four true external I/O boundaries are mocked:
- analyzer.dedup.redis_lib.Redis.from_url        (Redis)
- analyzer.openai_client.OpenAI                  (OpenAI SDK client)
- analyzer.slack_client.requests.post            (Slack webhook HTTP call)
- analyzer.dashboard_client.requests.post        (dashboard HTTP call)

process_error, dedup.is_duplicate, openai_client.analyze_error,
slack_client.send_notification and dashboard_client.store_error all run for
real. This is the regression guard for the exact class of bug the sibling
watcher<->dashboard integration review caught: the analyzer -> dashboard
payload is validated against dashboard's REAL Pydantic model (ErrorIn), not
just analyzer's own assumptions about that shape.

This relies on Starlette's TestClient running BackgroundTasks synchronously
before client.post(...) returns (verified directly against this project's
installed fastapi==0.128.8 / starlette==0.49.3 -- a plain FastAPI app with a
time.sleep()-ing background task showed the side effect was visible
immediately after the response came back).

Note on the two "requests.post" targets: analyzer.slack_client and
analyzer.dashboard_client both do a plain `import requests`, so
`analyzer.slack_client.requests` and `analyzer.dashboard_client.requests`
are literally the same module object and `requests.post` is one shared
attribute (verified directly: `slack_client.requests is dashboard_client.requests`
is True). Patching both "analyzer.slack_client.requests.post" and
"analyzer.dashboard_client.requests.post" inside the same `with` block would
have the second patch silently clobber the first for the whole block, so
every call -- from both send_notification and store_error -- would land on
whichever mock was entered last, leaving the other mock with zero calls.
To fake this one real boundary correctly, we patch "requests.post" a single
time and distinguish the two logical call sites by inspecting the URL each
call was made with.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.routes.api import ErrorIn as DashboardErrorIn

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


def _fake_openai_client(content="원인: 널 체크 누락"):
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    return fake_client


def _run_e2e(redis_set_return):
    fake_redis = MagicMock()
    fake_redis.set.return_value = redis_set_return

    app = create_app(CONFIG)
    client = TestClient(app)

    with patch("analyzer.dedup.redis_lib.Redis.from_url", return_value=fake_redis), \
         patch("analyzer.openai_client.OpenAI", return_value=_fake_openai_client()), \
         patch("analyzer.dashboard_client.requests.post") as mock_post:
        mock_post.return_value.status_code = 200

        response = client.post(
            "/api/errors", json=PAYLOAD, headers={"X-API-Key": "test-key"}
        )

    slack_calls = [c for c in mock_post.call_args_list if "hooks.slack.com" in c.args[0]]
    dashboard_calls = [c for c in mock_post.call_args_list if "/api/errors" in c.args[0]]
    return response, slack_calls, dashboard_calls


def test_e2e_not_duplicate_notifies_slack_and_stores_valid_dashboard_payload():
    response, slack_calls, dashboard_calls = _run_e2e(redis_set_return=True)

    assert response.status_code == 202
    assert len(slack_calls) == 1
    assert len(dashboard_calls) == 1

    _, kwargs = dashboard_calls[0]
    captured_payload = kwargs["json"]

    # Regression guard: the exact payload analyzer sends must validate
    # against dashboard's real Pydantic model.
    validated = DashboardErrorIn(**captured_payload)
    assert validated.notified is True
    assert captured_payload["notified"] is True


def test_e2e_duplicate_skips_slack_but_still_stores_with_notified_false():
    response, slack_calls, dashboard_calls = _run_e2e(redis_set_return=None)

    assert response.status_code == 202
    assert len(slack_calls) == 0
    assert len(dashboard_calls) == 1

    _, kwargs = dashboard_calls[0]
    captured_payload = kwargs["json"]

    validated = DashboardErrorIn(**captured_payload)
    assert validated.notified is False
    assert captured_payload["notified"] is False
