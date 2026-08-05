import os
import time
from unittest.mock import MagicMock

from watcher.main import LogFileHandler
from watcher.models import ServerConfig
from watcher.parser import ErrorEventAccumulator, LogParser

CONFIG = ServerConfig(
    server_id="server-a",
    log_path="",  # filled in per test with tmp_path
    format="default",
    central_endpoint="https://collector.example.com/api/errors",
    api_key_env="WATCHER_API_KEY",
)


def _make_handler(log_path):
    config = ServerConfig(**{**CONFIG.__dict__, "log_path": log_path})
    parser = LogParser(config)
    accumulator = ErrorEventAccumulator(parser)
    sender = MagicMock()
    handler = LogFileHandler(log_path, accumulator, sender)
    return handler, sender


class FakeEvent:
    def __init__(self, src_path):
        self.src_path = src_path


def test_read_new_lines_sends_completed_error_event(tmp_path):
    log_path = str(tmp_path / "application.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("2026-08-05 12:34:56.789  INFO 12345 --- [main] com.example.demo.App : Starting App\n")

    handler, sender = _make_handler(log_path)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(
            '2026-08-05 12:35:01.123 ERROR 12345 --- [nio-8080-exec-1] com.example.demo.MyService : boom\n'
        )
        f.write("java.lang.RuntimeException: boom\n")
        f.write(
            "2026-08-05 12:35:05.001  INFO 12345 --- [nio-8080-exec-2] com.example.demo.App : Recovered\n"
        )

    handler.on_modified(FakeEvent(log_path))

    sender.send.assert_called_once()
    sent_event = sender.send.call_args[0][0]
    assert sent_event.error_type == "java.lang.RuntimeException"


def test_ignores_events_for_other_files(tmp_path):
    log_path = str(tmp_path / "application.log")
    other_path = str(tmp_path / "other.log")
    open(log_path, "w").close()

    handler, sender = _make_handler(log_path)
    handler.on_modified(FakeEvent(other_path))

    sender.send.assert_not_called()


def test_partial_line_write_is_not_corrupted_or_lost(tmp_path):
    log_path = str(tmp_path / "application.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("2026-08-05 12:34:56.789  INFO 12345 --- [main] com.example.demo.App : Starting App\n")

    handler, sender = _make_handler(log_path)

    error_line = (
        '2026-08-05 12:35:01.123 ERROR 12345 --- [nio-8080-exec-1] '
        'com.example.demo.MyService : boom\n'
    )
    # Split the exception class name mid-word, with no trailing newline yet,
    # simulating a watchdog on_modified firing while the writer is mid-line.
    exception_line_first_part = "java.lang.RuntimeExcep"
    exception_line_second_part = "tion: boom\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(error_line)
        f.write(exception_line_first_part)

    handler.on_modified(FakeEvent(log_path))

    # The ERROR line itself is complete, but the exception line is still
    # mid-write, so no event should have completed yet.
    sender.send.assert_not_called()

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(exception_line_second_part)
        f.write(
            "2026-08-05 12:35:05.001  INFO 12345 --- [nio-8080-exec-2] com.example.demo.App : Recovered\n"
        )

    handler.on_modified(FakeEvent(log_path))

    sender.send.assert_called_once()
    sent_event = sender.send.call_args[0][0]
    assert sent_event.error_type == "java.lang.RuntimeException"
    assert sent_event.stack_trace == "java.lang.RuntimeException: boom"


def test_rotation_resets_read_position(tmp_path):
    log_path = str(tmp_path / "application.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("2026-08-05 12:34:56.789  INFO 12345 --- [main] com.example.demo.App : line one\n")

    handler, sender = _make_handler(log_path)
    handler.on_modified(FakeEvent(log_path))

    os.remove(log_path)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(
            '2026-08-05 12:36:00.000 ERROR 12345 --- [main] com.example.demo.App : after rotation\n'
        )
        f.write("java.lang.IllegalStateException: after rotation\n")
        f.write("2026-08-05 12:36:05.000  INFO 12345 --- [main] com.example.demo.App : done\n")

    handler.on_created(FakeEvent(log_path))

    sender.send.assert_called_once()
    sent_event = sender.send.call_args[0][0]
    assert sent_event.error_type == "java.lang.IllegalStateException"
