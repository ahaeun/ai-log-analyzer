from unittest.mock import MagicMock, patch

import redis as redis_lib
from watcher.models import ErrorEvent

from analyzer.dedup import is_duplicate

EVENT = ErrorEvent(
    server_id="server-a", timestamp="2026-08-06T10:00:00+09:00", log_level="ERROR",
    error_type="java.lang.NullPointerException", message="boom", stack_trace="at ...",
    raw_log="raw",
)


def test_first_occurrence_is_not_duplicate():
    fake_client = MagicMock()
    fake_client.set.return_value = True

    with patch("analyzer.dedup.redis_lib.Redis.from_url", return_value=fake_client):
        result = is_duplicate("redis://localhost:6379/0", EVENT)

    assert result is False
    _, kwargs = fake_client.set.call_args
    assert kwargs["nx"] is True
    assert kwargs["ex"] == 600


def test_repeated_occurrence_is_duplicate():
    fake_client = MagicMock()
    fake_client.set.return_value = None

    with patch("analyzer.dedup.redis_lib.Redis.from_url", return_value=fake_client):
        result = is_duplicate("redis://localhost:6379/0", EVENT)

    assert result is True


def test_different_events_get_different_keys():
    fake_client = MagicMock()
    fake_client.set.return_value = True

    with patch("analyzer.dedup.redis_lib.Redis.from_url", return_value=fake_client):
        is_duplicate("redis://localhost:6379/0", EVENT)
        other_event = ErrorEvent(
            server_id=EVENT.server_id, timestamp=EVENT.timestamp, log_level=EVENT.log_level,
            error_type="java.lang.RuntimeException", message=EVENT.message,
            stack_trace=EVENT.stack_trace, raw_log=EVENT.raw_log,
        )
        is_duplicate("redis://localhost:6379/0", other_event)

    first_key = fake_client.set.call_args_list[0][0][0]
    second_key = fake_client.set.call_args_list[1][0][0]
    assert first_key != second_key


def test_redis_connection_failure_treated_as_not_duplicate():
    fake_client = MagicMock()
    fake_client.set.side_effect = redis_lib.RedisError("connection failed")

    with patch("analyzer.dedup.redis_lib.Redis.from_url", return_value=fake_client):
        result = is_duplicate("redis://localhost:6379/0", EVENT)

    assert result is False
