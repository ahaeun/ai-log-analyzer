import json
import os
import threading
from unittest.mock import patch

import pytest
import requests

from watcher.models import ErrorEvent, ServerConfig
from watcher.sender import EventSender, next_retry_interval

CONFIG = ServerConfig(
    server_id="server-a",
    log_path="/var/log/app/application.log",
    format="default",
    central_endpoint="https://collector.example.com/api/errors",
    api_key_env="WATCHER_API_KEY",
)

EVENT = ErrorEvent(
    server_id="server-a",
    timestamp="2026-08-05T12:35:01+09:00",
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
    sender = EventSender(CONFIG, queue_dir=str(tmp_path))

    with patch("watcher.sender.requests.post", return_value=FakeResponse(200)) as mock_post:
        result = sender.send(EVENT)

    assert result is True
    assert not os.path.exists(sender.queue_path)
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["X-API-Key"] == "test-key"


def test_send_server_error_enqueues_for_retry(tmp_path):
    sender = EventSender(CONFIG, queue_dir=str(tmp_path))

    with patch("watcher.sender.requests.post", return_value=FakeResponse(500)):
        result = sender.send(EVENT)

    assert result is False
    assert os.path.exists(sender.queue_path)
    with open(sender.queue_path) as f:
        queued = json.loads(f.readline())
    assert queued["server_id"] == "server-a"


def test_send_client_error_does_not_enqueue(tmp_path):
    sender = EventSender(CONFIG, queue_dir=str(tmp_path))

    with patch("watcher.sender.requests.post", return_value=FakeResponse(401)):
        result = sender.send(EVENT)

    assert result is False
    assert not os.path.exists(sender.queue_path)


def test_send_network_error_enqueues_for_retry(tmp_path):
    sender = EventSender(CONFIG, queue_dir=str(tmp_path))

    with patch("watcher.sender.requests.post", side_effect=requests.ConnectionError):
        result = sender.send(EVENT)

    assert result is False
    assert os.path.exists(sender.queue_path)


def test_flush_queue_retries_and_clears_on_success(tmp_path):
    sender = EventSender(CONFIG, queue_dir=str(tmp_path))
    with patch("watcher.sender.requests.post", return_value=FakeResponse(500)):
        sender.send(EVENT)
    assert os.path.exists(sender.queue_path)

    with patch("watcher.sender.requests.post", return_value=FakeResponse(200)):
        has_remaining = sender.flush_queue()

    assert has_remaining is False
    with open(sender.queue_path) as f:
        assert f.read() == ""


def test_flush_queue_keeps_events_that_still_fail(tmp_path):
    sender = EventSender(CONFIG, queue_dir=str(tmp_path))
    with patch("watcher.sender.requests.post", return_value=FakeResponse(500)):
        sender.send(EVENT)

    with patch("watcher.sender.requests.post", return_value=FakeResponse(503)):
        has_remaining = sender.flush_queue()

    assert has_remaining is True
    with open(sender.queue_path) as f:
        assert json.loads(f.readline())["server_id"] == "server-a"


def test_concurrent_send_does_not_lose_queued_events(tmp_path):
    sender = EventSender(CONFIG, queue_dir=str(tmp_path))

    thread_count = 20
    threads = []
    stop_flushing = threading.Event()

    # Also hammer flush_queue() concurrently from a background thread, the way
    # the real retry-loop thread would. requests.post always returns 500, so
    # flush_queue() classifies every event as RETRY and never actually removes
    # anything - it only exercises the read-modify-write race against
    # concurrent _enqueue() calls without changing the expected final count.
    def flush_repeatedly():
        while not stop_flushing.is_set():
            sender.flush_queue()

    with patch("watcher.sender.requests.post", return_value=FakeResponse(500)):
        flusher = threading.Thread(target=flush_repeatedly)
        flusher.start()

        for _ in range(thread_count):
            t = threading.Thread(target=sender.send, args=(EVENT,))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stop_flushing.set()
        flusher.join()

    with open(sender.queue_path) as f:
        lines = [line for line in f.read().splitlines() if line]
    assert len(lines) == thread_count


def test_missing_api_key_env_raises_value_error(tmp_path, monkeypatch):
    monkeypatch.delenv("WATCHER_API_KEY", raising=False)

    with pytest.raises(ValueError):
        EventSender(CONFIG, queue_dir=str(tmp_path))


def test_next_retry_interval_doubles_up_to_cap():
    assert next_retry_interval(30, True) == 60
    assert next_retry_interval(240, True) == 300
    assert next_retry_interval(280, True, max_seconds=300) == 300


def test_next_retry_interval_resets_when_queue_empty():
    assert next_retry_interval(240, False) == 30
