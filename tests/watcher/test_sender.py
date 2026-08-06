import glob
import json
import os
import threading
from unittest.mock import patch

import pytest
import requests

from watcher.models import ErrorEvent, WatcherConfig
from watcher.sender import EventSender, next_retry_interval

CONFIG_KWARGS = dict(
    registry_url="http://dashboard/api/servers",
    analyzer_endpoint="https://collector.example.com/api/errors",
    api_key_env="WATCHER_API_KEY",
)


def _make_config(tmp_path):
    return WatcherConfig(queue_dir=str(tmp_path / "queue"), **CONFIG_KWARGS)


def _make_event(server_id="server-a"):
    return ErrorEvent(
        server_id=server_id,
        timestamp="2026-08-06T12:35:01+09:00",
        log_level="ERROR",
        error_type="java.lang.NullPointerException",
        message="Cannot invoke",
        stack_trace="at ...",
        raw_log="full raw log",
    )


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def api_key_env():
    os.environ["WATCHER_API_KEY"] = "test-key"
    yield
    del os.environ["WATCHER_API_KEY"]


def test_send_success_does_not_enqueue(tmp_path):
    config = _make_config(tmp_path)
    sender = EventSender(config)

    with patch("watcher.sender.requests.post", return_value=FakeResponse(200)) as mock_post:
        result = sender.send(_make_event())

    assert result is True
    assert glob.glob(os.path.join(config.queue_dir, "*.jsonl")) == []
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["X-API-Key"] == "test-key"


def test_send_server_error_enqueues_to_server_specific_file(tmp_path):
    config = _make_config(tmp_path)
    sender = EventSender(config)

    with patch("watcher.sender.requests.post", return_value=FakeResponse(500)):
        result = sender.send(_make_event(server_id="server-a"))

    assert result is False
    queue_path = os.path.join(config.queue_dir, "server-a.jsonl")
    assert os.path.exists(queue_path)
    with open(queue_path) as f:
        assert json.loads(f.readline())["server_id"] == "server-a"


def test_send_client_error_does_not_enqueue(tmp_path):
    config = _make_config(tmp_path)
    sender = EventSender(config)

    with patch("watcher.sender.requests.post", return_value=FakeResponse(401)):
        result = sender.send(_make_event())

    assert result is False
    assert glob.glob(os.path.join(config.queue_dir, "*.jsonl")) == []


def test_send_network_error_enqueues(tmp_path):
    config = _make_config(tmp_path)
    sender = EventSender(config)

    with patch("watcher.sender.requests.post", side_effect=requests.ConnectionError):
        result = sender.send(_make_event())

    assert result is False
    assert glob.glob(os.path.join(config.queue_dir, "*.jsonl"))


def test_flush_queue_keeps_separate_server_files_independent(tmp_path):
    config = _make_config(tmp_path)
    sender = EventSender(config)

    with patch("watcher.sender.requests.post", return_value=FakeResponse(500)):
        sender.send(_make_event(server_id="server-a"))
        sender.send(_make_event(server_id="server-b"))

    def post_side_effect(url, json, headers, timeout):
        if json["server_id"] == "server-a":
            return FakeResponse(200)
        return FakeResponse(503)

    with patch("watcher.sender.requests.post", side_effect=post_side_effect):
        has_remaining = sender.flush_queue()

    assert has_remaining is True
    with open(os.path.join(config.queue_dir, "server-a.jsonl")) as f:
        assert f.read() == ""
    with open(os.path.join(config.queue_dir, "server-b.jsonl")) as f:
        assert json.loads(f.readline())["server_id"] == "server-b"


def test_missing_api_key_env_raises_at_construction(tmp_path, monkeypatch):
    monkeypatch.delenv("WATCHER_API_KEY", raising=False)
    config = _make_config(tmp_path)

    with pytest.raises(ValueError, match="WATCHER_API_KEY"):
        EventSender(config)


def test_concurrent_send_and_flush_do_not_lose_events(tmp_path):
    config = _make_config(tmp_path)
    sender = EventSender(config)

    with patch("watcher.sender.requests.post", return_value=FakeResponse(500)):
        threads = [
            threading.Thread(target=sender.send, args=(_make_event(server_id="server-a"),))
            for _ in range(20)
        ]
        flush_thread = threading.Thread(target=sender.flush_queue)
        for t in threads:
            t.start()
        flush_thread.start()
        for t in threads:
            t.join()
        flush_thread.join()

    queue_path = os.path.join(config.queue_dir, "server-a.jsonl")
    with open(queue_path) as f:
        lines = [line for line in f.read().splitlines() if line]
    assert len(lines) == 20


def test_lock_for_is_isolated_per_server_but_stable_per_server(tmp_path):
    config = _make_config(tmp_path)
    sender = EventSender(config)

    lock_a1 = sender._lock_for("server-a")
    lock_a2 = sender._lock_for("server-a")
    lock_b = sender._lock_for("server-b")

    assert lock_a1 is lock_a2
    assert lock_a1 is not lock_b


def test_next_retry_interval_doubles_up_to_cap():
    assert next_retry_interval(30, True) == 60
    assert next_retry_interval(240, True) == 300


def test_next_retry_interval_resets_when_queue_empty():
    assert next_retry_interval(240, False) == 30
