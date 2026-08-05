import pytest

from watcher.models import ServerConfig
from watcher.parser import ErrorEventAccumulator, LogParser

DEFAULT_CONFIG = ServerConfig(
    server_id="server-a",
    log_path="/var/log/app/application.log",
    format="default",
    central_endpoint="https://collector.example.com/api/errors",
    api_key_env="WATCHER_API_KEY",
)

CUSTOM_CONFIG = ServerConfig(
    server_id="server-b",
    log_path="/var/log/app/custom.log",
    format="custom",
    custom_pattern=r"^(?P<timestamp>\S+)\s+(?P<level>\w+)\s+(?P<logger>\S+)\s+(?P<message>.*)$",
    central_endpoint="https://collector.example.com/api/errors",
    api_key_env="WATCHER_API_KEY",
)

DEFAULT_LOG_LINES = [
    "2026-08-05 12:34:56.789  INFO 12345 --- [main] com.example.demo.App : Starting App",
    '2026-08-05 12:35:01.123 ERROR 12345 --- [nio-8080-exec-1] com.example.demo.MyService : Cannot invoke "String.length()" because "s" is null',
    'java.lang.NullPointerException: Cannot invoke "String.length()" because "s" is null',
    "\tat com.example.demo.MyService.doSomething(MyService.java:42)",
    "\tat com.example.demo.MyController.handle(MyController.java:20)",
    "Caused by: java.lang.IllegalStateException: root cause",
    "\tat com.example.demo.MyService.helper(MyService.java:55)",
    "2026-08-05 12:35:05.001  INFO 12345 --- [nio-8080-exec-2] com.example.demo.App : Recovered",
]


def _feed_all(accumulator, lines):
    events = []
    for line in lines:
        event = accumulator.feed_line(line)
        if event is not None:
            events.append(event)
    return events


def test_default_format_groups_one_error_event():
    parser = LogParser(DEFAULT_CONFIG)
    accumulator = ErrorEventAccumulator(parser)

    events = _feed_all(accumulator, DEFAULT_LOG_LINES)

    assert len(events) == 1
    event = events[0]
    assert event.server_id == "server-a"
    assert event.log_level == "ERROR"
    assert event.error_type == "java.lang.NullPointerException"
    assert "Cannot invoke" in event.message
    assert "Caused by: java.lang.IllegalStateException" in event.stack_trace
    assert event.raw_log.startswith("2026-08-05 12:35:01.123 ERROR")


def test_info_only_lines_produce_no_event():
    parser = LogParser(DEFAULT_CONFIG)
    accumulator = ErrorEventAccumulator(parser)

    events = _feed_all(accumulator, DEFAULT_LOG_LINES[:1] + DEFAULT_LOG_LINES[-1:])

    assert events == []


def test_unmatched_line_before_any_entry_is_ignored():
    parser = LogParser(DEFAULT_CONFIG)
    accumulator = ErrorEventAccumulator(parser)

    events = _feed_all(accumulator, ["garbage line with no timestamp prefix"] + DEFAULT_LOG_LINES)

    assert len(events) == 1


def test_flush_emits_pending_error_at_end_of_stream():
    parser = LogParser(DEFAULT_CONFIG)
    accumulator = ErrorEventAccumulator(parser)

    events = _feed_all(accumulator, DEFAULT_LOG_LINES[:3])  # ends mid-stack-trace, no next entry
    assert events == []

    final_event = accumulator.flush()
    assert final_event is not None
    assert final_event.error_type == "java.lang.NullPointerException"


def test_custom_format_parses_with_named_groups():
    parser = LogParser(CUSTOM_CONFIG)
    accumulator = ErrorEventAccumulator(parser)

    lines = [
        "2026-08-05T12:00:00 ERROR svc.Worker Something exploded: java.lang.RuntimeException",
        "\tat svc.Worker.run(Worker.java:10)",
        "2026-08-05T12:00:05 INFO svc.Worker back to normal",
    ]

    events = _feed_all(accumulator, lines)

    assert len(events) == 1
    assert events[0].server_id == "server-b"
    assert events[0].error_type == "java.lang.RuntimeException"
