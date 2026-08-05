# log-watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `watcher/` package — a per-server Python agent that watches one Spring Boot log file, groups error/exception lines into structured events, and ships them to the central analyzer over HTTP with local buffering on failure.

**Architecture:** A YAML config per server (`ServerConfig`) selects a default or custom regex for parsing (`LogParser`). A stateful `ErrorEventAccumulator` turns a stream of log lines into completed `ErrorEvent` objects one line at a time, so it can be fed directly from a `watchdog` file-system handler in `main.py`. `EventSender` posts each completed event to the central endpoint and falls back to an append-only JSONL queue file, retried on a backoff loop, when delivery fails.

**Tech Stack:** Python 3, `watchdog` (file monitoring), `requests` (HTTP), `PyYAML` (config), `pytest` (tests).

## Global Constraints

- API 키(및 기타 인증 정보)는 하드코딩하지 않고 설정 파일의 `api_key_env`가 가리키는 환경변수에서만 로드한다.
- 이번 계획의 담당 범위는 `watcher/`와 그 테스트(`tests/watcher/`)로 한정한다. `analyzer/`, `dashboard/`는 다루지 않는다.
- 설정 파일 파싱 실패(필수 필드 누락, 잘못된 정규식, `custom_pattern`에 필요한 named group 누락)는 즉시 예외를 발생시켜 실패시킨다 — 조용히 무시하지 않는다.
- 에러 이벤트 스키마는 정확히 다음 필드를 갖는다: `server_id`, `timestamp`, `log_level`, `error_type`, `message`, `stack_trace`, `raw_log`.

---

### Task 1: 프로젝트 스캐폴딩

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `watcher/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/watcher/__init__.py`

**Interfaces:**
- Produces: 이후 모든 태스크가 의존하는 패키지 레이아웃과 의존성.

- [ ] **Step 1: 디렉터리와 의존성 파일 생성**

`requirements.txt`:
```
watchdog>=3.0.0
requests>=2.31.0
PyYAML>=6.0
pytest>=7.4.0
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
```

`watcher/__init__.py`: (빈 파일)

`tests/__init__.py`: (빈 파일)

`tests/watcher/__init__.py`: (빈 파일)

- [ ] **Step 2: 의존성 설치**

Run: `pip install -r requirements.txt`
Expected: 설치 성공, 에러 없음

- [ ] **Step 3: pytest가 빈 스위트를 정상적으로 인식하는지 확인**

Run: `pytest`
Expected: `no tests ran` (수집 에러 없이 종료)

- [ ] **Step 4: Commit**

```bash
git add requirements.txt pytest.ini watcher/__init__.py tests/__init__.py tests/watcher/__init__.py
git commit -m "chore: scaffold watcher package and test layout"
```

---

### Task 2: 설정 파일 로더 (`models.py`, `config.py`)

**Files:**
- Create: `watcher/models.py`
- Create: `watcher/config.py`
- Test: `tests/watcher/test_config.py`

**Interfaces:**
- Consumes: 없음 (최초 태스크)
- Produces:
  - `ServerConfig(server_id, log_path, format, central_endpoint, api_key_env, custom_pattern=None)` — dataclass, `format`이 `'default'`/`'custom'` 외의 값이거나 `format == 'custom'`인데 `custom_pattern`이 없으면 `ValueError`
  - `ErrorEvent(server_id, timestamp, log_level, error_type, message, stack_trace, raw_log)` — dataclass, `.to_dict()` 제공
  - `load_server_config(path: str) -> ServerConfig` — YAML 로드 + 필수 필드/정규식 유효성 검사, 실패 시 `ValueError`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/watcher/test_config.py`:
```python
import pytest
from watcher.config import load_server_config


def _write_yaml(tmp_path, content):
    path = tmp_path / "server.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_load_default_format_config(tmp_path):
    path = _write_yaml(tmp_path, """
server_id: server-a
log_path: /var/log/app/application.log
format: default
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    config = load_server_config(path)

    assert config.server_id == "server-a"
    assert config.log_path == "/var/log/app/application.log"
    assert config.format == "default"
    assert config.custom_pattern is None
    assert config.central_endpoint == "https://collector.example.com/api/errors"
    assert config.api_key_env == "WATCHER_API_KEY"


def test_load_custom_format_config(tmp_path):
    path = _write_yaml(tmp_path, r"""
server_id: server-b
log_path: /var/log/app/custom.log
format: custom
custom_pattern: '^(?P<timestamp>\S+)\s+(?P<level>\w+)\s+(?P<logger>\S+)\s+(?P<message>.*)$'
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    config = load_server_config(path)

    assert config.format == "custom"
    assert config.custom_pattern is not None


def test_missing_required_field_raises(tmp_path):
    path = _write_yaml(tmp_path, """
server_id: server-a
log_path: /var/log/app/application.log
format: default
""")

    with pytest.raises(ValueError, match="Missing required config fields"):
        load_server_config(path)


def test_custom_format_without_pattern_raises(tmp_path):
    path = _write_yaml(tmp_path, """
server_id: server-a
log_path: /var/log/app/application.log
format: custom
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    with pytest.raises(ValueError, match="custom_pattern is required"):
        load_server_config(path)


def test_custom_format_missing_named_groups_raises(tmp_path):
    path = _write_yaml(tmp_path, r"""
server_id: server-a
log_path: /var/log/app/application.log
format: custom
custom_pattern: '^(?P<message>.*)$'
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY
""")

    with pytest.raises(ValueError, match="custom_pattern must define named groups"):
        load_server_config(path)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watcher.config'`

- [ ] **Step 3: `models.py` 구현**

`watcher/models.py`:
```python
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ServerConfig:
    server_id: str
    log_path: str
    format: str
    central_endpoint: str
    api_key_env: str
    custom_pattern: Optional[str] = None

    def __post_init__(self):
        if self.format not in ("default", "custom"):
            raise ValueError(
                f"format must be 'default' or 'custom', got {self.format!r}"
            )
        if self.format == "custom" and not self.custom_pattern:
            raise ValueError("custom_pattern is required when format is 'custom'")


@dataclass
class ErrorEvent:
    server_id: str
    timestamp: str
    log_level: str
    error_type: str
    message: str
    stack_trace: str
    raw_log: str

    def to_dict(self):
        return asdict(self)
```

- [ ] **Step 4: `config.py` 구현**

`watcher/config.py`:
```python
import re

import yaml

from watcher.models import ServerConfig

REQUIRED_FIELDS = ("server_id", "log_path", "format", "central_endpoint", "api_key_env")
REQUIRED_CUSTOM_GROUPS = {"timestamp", "level", "message"}


def load_server_config(path: str) -> ServerConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    custom_pattern = data.get("custom_pattern")
    if data["format"] == "custom":
        if not custom_pattern:
            raise ValueError("custom_pattern is required when format is 'custom'")
        compiled = re.compile(custom_pattern)
        missing_groups = REQUIRED_CUSTOM_GROUPS - set(compiled.groupindex.keys())
        if missing_groups:
            raise ValueError(
                f"custom_pattern must define named groups: {sorted(missing_groups)}"
            )

    return ServerConfig(
        server_id=data["server_id"],
        log_path=data["log_path"],
        format=data["format"],
        central_endpoint=data["central_endpoint"],
        api_key_env=data["api_key_env"],
        custom_pattern=custom_pattern,
    )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/watcher/test_config.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add watcher/models.py watcher/config.py tests/watcher/test_config.py
git commit -m "feat: add server config loader with validation"
```

---

### Task 3: 로그 파서 및 에러 이벤트 누산기 (`parser.py`)

**Files:**
- Create: `watcher/parser.py`
- Test: `tests/watcher/test_parser.py`

**Interfaces:**
- Consumes: `ServerConfig`, `ErrorEvent` (Task 2)
- Produces:
  - `LogParser(config: ServerConfig)` — `.match_entry(line) -> Optional[re.Match]`, `.is_error_start(match) -> bool`, `.build_event(lines: list[str]) -> ErrorEvent`
  - `ErrorEventAccumulator(parser: LogParser)` — `.feed_line(line: str) -> Optional[ErrorEvent]`, `.flush() -> Optional[ErrorEvent]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/watcher/test_parser.py`:
```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watcher.parser'`

- [ ] **Step 3: `parser.py` 구현**

`watcher/parser.py`:
```python
import re
from datetime import datetime

from watcher.models import ErrorEvent

DEFAULT_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<level>[A-Z]+)\s+\d+\s+---\s+\[[^\]]*\]\s+"
    r"(?P<logger>\S+)\s*:\s*(?P<message>.*)$"
)

ERROR_TYPE_PATTERN = re.compile(r"([\w.$]+(?:Exception|Error))")


class LogParser:
    def __init__(self, config):
        self.server_id = config.server_id
        self._entry_pattern = (
            DEFAULT_PATTERN if config.format == "default" else re.compile(config.custom_pattern)
        )

    def match_entry(self, line):
        return self._entry_pattern.match(line)

    def is_error_start(self, match):
        if match is None:
            return False
        groupdict = match.groupdict()
        level = groupdict.get("level", "")
        message = groupdict.get("message", "")
        return level == "ERROR" or "Exception" in message or "Caused by:" in message

    def build_event(self, lines):
        raw_log = "\n".join(lines)
        match = self.match_entry(lines[0])
        groupdict = match.groupdict() if match else {}

        type_match = ERROR_TYPE_PATTERN.search(raw_log)

        return ErrorEvent(
            server_id=self.server_id,
            timestamp=self._normalize_timestamp(groupdict.get("timestamp")),
            log_level=groupdict.get("level", "ERROR"),
            error_type=type_match.group(1) if type_match else "UNKNOWN",
            message=groupdict.get("message", lines[0]),
            stack_trace="\n".join(lines[1:]),
            raw_log=raw_log,
        )

    @staticmethod
    def _normalize_timestamp(raw_timestamp):
        if not raw_timestamp:
            return datetime.now().astimezone().isoformat(timespec="seconds")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                naive = datetime.strptime(raw_timestamp, fmt)
                return naive.astimezone().isoformat(timespec="seconds")
            except ValueError:
                continue
        return raw_timestamp


class ErrorEventAccumulator:
    """Groups a stream of log lines into ErrorEvent objects, one line at a time."""

    def __init__(self, parser: LogParser):
        self._parser = parser
        self._pending_lines = None

    def feed_line(self, line):
        match = self._parser.match_entry(line)

        if match is not None:
            completed = self._finalize_pending()
            if self._parser.is_error_start(match):
                self._pending_lines = [line]
            return completed

        if self._pending_lines is not None:
            self._pending_lines.append(line)

        return None

    def flush(self):
        return self._finalize_pending()

    def _finalize_pending(self):
        if self._pending_lines is None:
            return None
        event = self._parser.build_event(self._pending_lines)
        self._pending_lines = None
        return event
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/watcher/test_parser.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add watcher/parser.py tests/watcher/test_parser.py
git commit -m "feat: add streaming log parser and error event accumulator"
```

---

### Task 4: HTTP 전송 및 재시도 큐 (`sender.py`)

**Files:**
- Create: `watcher/sender.py`
- Test: `tests/watcher/test_sender.py`

**Interfaces:**
- Consumes: `ServerConfig`, `ErrorEvent` (Task 2)
- Produces:
  - `DeliveryResult` enum: `DELIVERED`, `RETRY`, `FAILED`
  - `next_retry_interval(current_interval, queue_still_has_events, base_seconds=30, max_seconds=300) -> int`
  - `EventSender(config: ServerConfig, queue_dir: str)` — `.send(event: ErrorEvent) -> bool`, `.flush_queue() -> bool`, `.run_retry_loop(interval_seconds=30, max_interval_seconds=300, stop_event=None)`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/watcher/test_sender.py`:
```python
import json
import os
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


def test_next_retry_interval_doubles_up_to_cap():
    assert next_retry_interval(30, True) == 60
    assert next_retry_interval(240, True) == 300
    assert next_retry_interval(280, True, max_seconds=300) == 300


def test_next_retry_interval_resets_when_queue_empty():
    assert next_retry_interval(240, False) == 30
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watcher.sender'`

- [ ] **Step 3: `sender.py` 구현**

`watcher/sender.py`:
```python
import json
import os
import time
from enum import Enum

import requests


class DeliveryResult(Enum):
    DELIVERED = "delivered"
    RETRY = "retry"
    FAILED = "failed"


def next_retry_interval(current_interval, queue_still_has_events, base_seconds=30, max_seconds=300):
    if queue_still_has_events:
        return min(current_interval * 2, max_seconds)
    return base_seconds


class EventSender:
    def __init__(self, config, queue_dir="watcher/.queue"):
        self.config = config
        os.makedirs(queue_dir, exist_ok=True)
        self.queue_path = os.path.join(queue_dir, f"{config.server_id}.jsonl")

    def send(self, event):
        result = self._post_event(event.to_dict())
        if result == DeliveryResult.RETRY:
            self._enqueue(event.to_dict())
        return result == DeliveryResult.DELIVERED

    def flush_queue(self):
        if not os.path.exists(self.queue_path):
            return False

        with open(self.queue_path, "r", encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line]

        remaining = []
        for line in lines:
            event_dict = json.loads(line)
            if self._post_event(event_dict) == DeliveryResult.RETRY:
                remaining.append(line)

        with open(self.queue_path, "w", encoding="utf-8") as f:
            for line in remaining:
                f.write(line + "\n")

        return len(remaining) > 0

    def run_retry_loop(self, interval_seconds=30, max_interval_seconds=300, stop_event=None):
        interval = interval_seconds
        while stop_event is None or not stop_event.is_set():
            time.sleep(interval)
            still_has_queue = self.flush_queue()
            interval = next_retry_interval(
                interval, still_has_queue, base_seconds=interval_seconds, max_seconds=max_interval_seconds
            )

    def _post_event(self, event_dict):
        try:
            response = requests.post(
                self.config.central_endpoint,
                json=event_dict,
                headers={"X-API-Key": self._api_key()},
                timeout=5,
            )
        except requests.RequestException:
            return DeliveryResult.RETRY

        if response.status_code < 300:
            return DeliveryResult.DELIVERED
        if response.status_code >= 500:
            return DeliveryResult.RETRY
        return DeliveryResult.FAILED

    def _api_key(self):
        return os.environ.get(self.config.api_key_env, "")

    def _enqueue(self, event_dict):
        with open(self.queue_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_dict) + "\n")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/watcher/test_sender.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add watcher/sender.py tests/watcher/test_sender.py
git commit -m "feat: add HTTP event sender with backoff retry queue"
```

---

### Task 5: watchdog 연동 (`main.py`)

**Files:**
- Create: `watcher/main.py`
- Test: `tests/watcher/test_main.py`

**Interfaces:**
- Consumes: `load_server_config` (Task 2), `LogParser`/`ErrorEventAccumulator` (Task 3), `EventSender` (Task 4)
- Produces:
  - `LogFileHandler(log_path: str, accumulator: ErrorEventAccumulator, sender: EventSender)` — watchdog `FileSystemEventHandler` subclass, tails only the configured file and forwards completed events to `sender.send(...)`
  - `run(config_path: str)` — wires config/parser/accumulator/sender/observer together and blocks until `KeyboardInterrupt`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/watcher/test_main.py`:
```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watcher.main'`

- [ ] **Step 3: `main.py` 구현**

`watcher/main.py`:
```python
import argparse
import os
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from watcher.config import load_server_config
from watcher.parser import ErrorEventAccumulator, LogParser
from watcher.sender import EventSender


class LogFileHandler(FileSystemEventHandler):
    def __init__(self, log_path, accumulator, sender):
        self.log_path = os.path.abspath(log_path)
        self.accumulator = accumulator
        self.sender = sender
        self._position = self._current_size()

    def on_modified(self, event):
        if os.path.abspath(event.src_path) != self.log_path:
            return
        self._read_new_lines()

    def on_created(self, event):
        if os.path.abspath(event.src_path) != self.log_path:
            return
        self._position = 0
        self._read_new_lines()

    def _current_size(self):
        return os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0

    def _read_new_lines(self):
        size = self._current_size()
        if size < self._position:
            self._position = 0

        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._position)
            new_lines = f.read().splitlines()
            self._position = f.tell()

        for line in new_lines:
            completed_event = self.accumulator.feed_line(line)
            if completed_event is not None:
                self.sender.send(completed_event)


def run(config_path):
    config = load_server_config(config_path)
    parser = LogParser(config)
    accumulator = ErrorEventAccumulator(parser)
    sender = EventSender(config)

    handler = LogFileHandler(config.log_path, accumulator, sender)
    observer = Observer()
    observer.schedule(handler, path=os.path.dirname(config.log_path) or ".", recursive=False)
    observer.start()

    stop_event = threading.Event()
    retry_thread = threading.Thread(
        target=sender.run_retry_loop, kwargs={"stop_event": stop_event}, daemon=True
    )
    retry_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        observer.stop()
    observer.join()


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Run the log-watcher agent for one server.")
    argparser.add_argument("config_path", help="Path to the server's YAML config file")
    args = argparser.parse_args()
    run(args.config_path)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/watcher/test_main.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 테스트 스위트 실행**

Run: `pytest -v`
Expected: 모든 테스트(Task 2~5, 총 21개) PASS

- [ ] **Step 6: Commit**

```bash
git add watcher/main.py tests/watcher/test_main.py
git commit -m "feat: wire watchdog file handler to parser and sender"
```

---

### Task 6: 에러 이벤트 스키마 메모리 문서화

**Files:**
- Create: `.claude/memory/log-watcher/error-event-schema.md`

**Interfaces:**
- Consumes: `ErrorEvent` 필드 정의 (Task 2), `analyzer` 설계 시 참조할 계약

- [ ] **Step 1: 스키마 문서 작성**

`.claude/memory/log-watcher/error-event-schema.md`:
```markdown
# 에러 이벤트 스키마 (log-watcher → analyzer)

- 작성일: 2026-08-05
- 관련 설계 문서: docs/superpowers/specs/2026-08-05-log-watcher-design.md
- 관련 구현: watcher/models.py의 ErrorEvent, watcher/sender.py가 이 스키마로 HTTP POST 전송

## 결론 요약
log-watcher는 아래 JSON 스키마로 에러 이벤트를 중앙 서버(analyzer)에 전송한다.
analyzer는 수집 API에서 이 필드를 그대로 받는다고 가정하고 설계해야 한다.

## 스키마

\`\`\`json
{
  "server_id": "server-a",
  "timestamp": "2026-08-05T12:35:01+09:00",
  "log_level": "ERROR",
  "error_type": "java.lang.NullPointerException",
  "message": "Cannot invoke ...",
  "stack_trace": "at com.example...\n...",
  "raw_log": "원본 로그 라인 전체 (스택트레이스 포함)"
}
\`\`\`

## 전송 방식
- HTTP POST, JSON body
- 헤더: `X-API-Key: <서버별 설정의 api_key_env가 가리키는 환경변수 값>`
- 전송 실패(네트워크 오류, timeout, 5xx) 시 워처가 로컬에서 재시도하므로, analyzer는 동일 이벤트가 지연되어 도착할 수 있음을 고려해야 한다 (중복 수신 자체는 없음 — 워처가 성공한 요청만 큐에서 제거).
```

- [ ] **Step 2: Commit**

```bash
git add .claude/memory/log-watcher/error-event-schema.md
git commit -m "docs: record error event schema for analyzer handoff"
```
