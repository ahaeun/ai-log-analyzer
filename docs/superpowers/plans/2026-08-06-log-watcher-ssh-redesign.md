# log-watcher SSH Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-server resident-agent `watcher/` package with a single central process that SSH-tails a dynamically-registered list of servers, so production servers need no Python installation.

**Architecture:** A shared `WatcherConfig` (registry URL, analyzer endpoint, shared API key env var name) is loaded once from a local YAML file. A `registry_client` periodically fetches the current server list (`ServerEntry` objects) from a dashboard HTTP endpoint. `WatcherManager` diffs that list against its active set, creating/removing one `SSHTailer` + `ErrorEventAccumulator` pair per server. Each poll cycle, every active tailer's new bytes are fed through the (unchanged) streaming parser, and completed events go through a modified `EventSender` that now manages one queue file per `server_id` instead of one per process.

**Tech Stack:** Python 3, `paramiko` (SSH, replaces `watchdog`), `requests` (HTTP), `PyYAML` (config), `pytest` (tests).

## Global Constraints

- API 키는 하드코딩하지 않고 `WatcherConfig.api_key_env`가 가리키는 환경변수에서만 로드한다. 이제 서버별이 아니라 watcher 프로세스 전체가 공유하는 하나의 키다.
- 담당 범위는 `watcher/`와 그 테스트(`tests/watcher/`)로 한정한다.
- watcher 자체의 YAML 설정 파싱 실패는 즉시 예외를 발생시켜 실패시킨다 — 조용히 무시하지 않는다.
- 레지스트리(dashboard) 조회 실패는 다르게 다룬다: fail-soft — 마지막으로 알고 있던 서버 목록을 유지하고 다음 주기에 재시도한다 (설정 파싱과 달리 런타임 중 일시적 장애이기 때문).
- 에러 이벤트 스키마는 변경되지 않는다: `server_id`, `timestamp`, `log_level`, `error_type`, `message`, `stack_trace`, `raw_log`.
- SSH 인증은 개인키 파일 경로(`ssh_key_path`)만 사용하며, 키 파일 자체는 네트워크로 전송되지 않는다.
- 서버 목록 항목 하나가 유효하지 않아도(정규식 오류 등) 나머지 유효한 항목은 정상 처리한다 — 전체 조회를 실패시키지 않는다.

## Migration Note on Test Scoping

Task 1에서 `watcher/models.py`의 `ServerConfig`를 제거하고 `ServerEntry`/`WatcherConfig`로 바꾼다. `tests/watcher/test_sender.py`와 `tests/watcher/test_main.py`는 옛 `ServerConfig`를 참조하므로 Task 4/5에서 교체되기 전까지는 깨진 상태다. **Task 1~4에서는 그 태스크가 다루는 파일의 테스트만 지정해서 실행**하고(`pytest tests/watcher/test_config.py tests/watcher/test_parser.py` 처럼), Task 5에서 마지막으로 전체 스위트(`pytest`)를 실행해 모든 것이 일관됨을 확인한다. 이는 기존 계획에서도 쓰인 패턴이다.

---

### Task 1: models.py 교체 + watcher 설정 로더 교체 + 의존성 갱신

**Files:**
- Modify: `watcher/models.py` (전체 교체)
- Modify: `watcher/config.py` (전체 교체)
- Modify: `requirements.txt`
- Modify: `tests/watcher/test_config.py` (전체 교체)
- Modify: `tests/watcher/test_parser.py` (fixture만 `ServerConfig` → `ServerEntry`로 교체, 나머지 테스트 로직은 무변경)

**Interfaces:**
- Consumes: 없음 (최초 태스크)
- Produces:
  - `ServerEntry(server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern=None)` — `format`이 `'default'`/`'custom'` 외 값이거나 `format=='custom'`인데 `custom_pattern`이 없으면 `ValueError`
  - `WatcherConfig(registry_url, analyzer_endpoint, api_key_env, queue_dir="watcher/.queue", registry_poll_interval=30, log_poll_interval=15)`
  - `ErrorEvent(server_id, timestamp, log_level, error_type, message, stack_trace, raw_log)` — 무변경
  - `load_watcher_config(path: str) -> WatcherConfig` — YAML 로드, 필수 필드(`registry_url`, `analyzer_endpoint`, `api_key_env`) 누락 시 `ValueError`

- [ ] **Step 1: `requirements.txt` 갱신**

```
requests>=2.31.0
PyYAML>=6.0
pytest>=7.4.0
paramiko>=3.4.0
```

Run: `python3 -m pip install -r requirements.txt`
Expected: 설치 성공, 에러 없음 (기존에 설치된 `watchdog`은 남아있어도 무방합니다 — Task 5에서 코드가 더 이상 그걸 참조하지 않게 됩니다)

- [ ] **Step 2: 실패하는 테스트 작성 — `tests/watcher/test_config.py` 전체 교체**

```python
import pytest

from watcher.config import load_watcher_config


def _write_yaml(tmp_path, content):
    path = tmp_path / "watcher.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_load_minimal_config_uses_defaults(tmp_path):
    path = _write_yaml(tmp_path, """
registry_url: http://dashboard.internal/api/servers
analyzer_endpoint: https://analyzer.internal/api/errors
api_key_env: WATCHER_API_KEY
""")

    config = load_watcher_config(path)

    assert config.registry_url == "http://dashboard.internal/api/servers"
    assert config.analyzer_endpoint == "https://analyzer.internal/api/errors"
    assert config.api_key_env == "WATCHER_API_KEY"
    assert config.queue_dir == "watcher/.queue"
    assert config.registry_poll_interval == 30
    assert config.log_poll_interval == 15


def test_load_config_with_overrides(tmp_path):
    path = _write_yaml(tmp_path, """
registry_url: http://dashboard.internal/api/servers
analyzer_endpoint: https://analyzer.internal/api/errors
api_key_env: WATCHER_API_KEY
queue_dir: /var/lib/watcher/queue
registry_poll_interval: 60
log_poll_interval: 5
""")

    config = load_watcher_config(path)

    assert config.queue_dir == "/var/lib/watcher/queue"
    assert config.registry_poll_interval == 60
    assert config.log_poll_interval == 5


def test_missing_required_field_raises(tmp_path):
    path = _write_yaml(tmp_path, """
registry_url: http://dashboard.internal/api/servers
""")

    with pytest.raises(ValueError, match="Missing required config fields"):
        load_watcher_config(path)
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/watcher/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_watcher_config' from 'watcher.config'` (옛 `load_server_config`만 존재)

- [ ] **Step 4: `watcher/models.py` 전체 교체**

```python
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ServerEntry:
    server_id: str
    host: str
    port: int
    username: str
    ssh_key_path: str
    log_path: str
    format: str
    custom_pattern: Optional[str] = None

    def __post_init__(self):
        if self.format not in ("default", "custom"):
            raise ValueError(
                f"format must be 'default' or 'custom', got {self.format!r}"
            )
        if self.format == "custom" and not self.custom_pattern:
            raise ValueError("custom_pattern is required when format is 'custom'")


@dataclass
class WatcherConfig:
    registry_url: str
    analyzer_endpoint: str
    api_key_env: str
    queue_dir: str = "watcher/.queue"
    registry_poll_interval: int = 30
    log_poll_interval: int = 15


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

- [ ] **Step 5: `watcher/config.py` 전체 교체**

```python
import yaml

from watcher.models import WatcherConfig

REQUIRED_FIELDS = ("registry_url", "analyzer_endpoint", "api_key_env")
OPTIONAL_FIELDS = ("queue_dir", "registry_poll_interval", "log_poll_interval")


def load_watcher_config(path: str) -> WatcherConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    kwargs = {field: data[field] for field in REQUIRED_FIELDS}
    for field in OPTIONAL_FIELDS:
        if field in data:
            kwargs[field] = data[field]

    return WatcherConfig(**kwargs)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/watcher/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: `tests/watcher/test_parser.py`의 fixture를 `ServerEntry`로 교체 (전체 파일 교체)**

`parser.py` 자체는 `config.server_id`/`config.format`/`config.custom_pattern`만 사용하므로 변경이 필요 없다 — 이 파일에서 바뀌는 것은 fixture 생성자뿐이다.

```python
import pytest

from watcher.models import ServerEntry
from watcher.parser import ErrorEventAccumulator, LogParser

DEFAULT_CONFIG = ServerEntry(
    server_id="server-a",
    host="10.0.1.10",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-a.pem",
    log_path="/var/log/app/application.log",
    format="default",
)

CUSTOM_CONFIG = ServerEntry(
    server_id="server-b",
    host="10.0.1.11",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-b.pem",
    log_path="/var/log/app/custom.log",
    format="custom",
    custom_pattern=r"^(?P<timestamp>\S+)\s+(?P<level>\w+)\s+(?P<logger>\S+)\s+(?P<message>.*)$",
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

    events = _feed_all(accumulator, DEFAULT_LOG_LINES[:3])
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

- [ ] **Step 8: 이 태스크 범위 테스트 통과 확인**

Run: `pytest tests/watcher/test_config.py tests/watcher/test_parser.py -v`
Expected: PASS (8 passed). (`test_sender.py`/`test_main.py`는 아직 옛 `ServerConfig`를 참조해 실패하지만, 이 태스크에서는 실행하지 않습니다 — Task 4/5에서 처리됩니다.)

- [ ] **Step 9: Commit**

```bash
git add watcher/models.py watcher/config.py requirements.txt tests/watcher/test_config.py tests/watcher/test_parser.py
git commit -m "refactor: replace per-server ServerConfig with ServerEntry/WatcherConfig for SSH redesign"
```

---

### Task 2: 서버 레지스트리 클라이언트 (`registry_client.py`)

**Files:**
- Create: `watcher/registry_client.py`
- Test: `tests/watcher/test_registry_client.py`

**Interfaces:**
- Consumes: `ServerEntry` (Task 1)
- Produces: `fetch_servers(registry_url: str) -> tuple[list[ServerEntry], list[tuple[str, str]]]` — 두 번째 값은 `(server_id 또는 "<unknown>", 에러 메시지)` 목록으로, 걸러진 항목을 나타냄. 레지스트리 자체가 응답하지 않으면 `requests.RequestException`을 그대로 전파한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
import pytest
import requests
from unittest.mock import patch

from watcher.registry_client import fetch_servers

VALID_ENTRY = {
    "server_id": "server-a",
    "host": "10.0.1.10",
    "port": 22,
    "username": "deploy",
    "ssh_key_path": "/home/watcher/.ssh/server-a.pem",
    "log_path": "/var/log/app/application.log",
    "format": "default",
    "custom_pattern": None,
}


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_servers_returns_valid_entries():
    with patch("watcher.registry_client.requests.get", return_value=FakeResponse([VALID_ENTRY])):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert len(servers) == 1
    assert servers[0].server_id == "server-a"
    assert skipped == []


def test_fetch_servers_skips_invalid_entry_but_keeps_valid_ones():
    invalid_entry = {**VALID_ENTRY, "server_id": "server-b", "format": "custom", "custom_pattern": None}
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse([VALID_ENTRY, invalid_entry]),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert len(servers) == 1
    assert servers[0].server_id == "server-a"
    assert len(skipped) == 1
    assert skipped[0][0] == "server-b"


def test_fetch_servers_skips_entry_missing_required_field():
    missing_field_entry = {k: v for k, v in VALID_ENTRY.items() if k != "host"}
    missing_field_entry["server_id"] = "server-c"
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse([missing_field_entry]),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert servers == []
    assert skipped[0][0] == "server-c"


def test_fetch_servers_propagates_registry_unreachable():
    with patch("watcher.registry_client.requests.get", side_effect=requests.ConnectionError):
        with pytest.raises(requests.ConnectionError):
            fetch_servers("http://dashboard/api/servers")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_registry_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watcher.registry_client'`

- [ ] **Step 3: `watcher/registry_client.py` 구현**

```python
import requests

from watcher.models import ServerEntry


def fetch_servers(registry_url):
    response = requests.get(registry_url, timeout=5)
    response.raise_for_status()
    data = response.json()

    servers = []
    skipped = []
    for item in data:
        try:
            servers.append(ServerEntry(**item))
        except (TypeError, ValueError) as e:
            skipped.append((item.get("server_id", "<unknown>"), str(e)))

    return servers, skipped
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/watcher/test_registry_client.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add watcher/registry_client.py tests/watcher/test_registry_client.py
git commit -m "feat: add server registry client for dashboard-driven server discovery"
```

---

### Task 3: SSH 원격 tail (`ssh_tail.py`)

**Files:**
- Create: `watcher/ssh_tail.py`
- Test: `tests/watcher/test_ssh_tail.py`

**Interfaces:**
- Consumes: `ServerEntry` (Task 1)
- Produces: `SSHTailer(entry: ServerEntry)` — `.read_new_bytes() -> str`, 내부적으로 연결을 유지하고 오프셋을 추적한다

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from unittest.mock import MagicMock, patch

from watcher.models import ServerEntry
from watcher.ssh_tail import SSHTailer

ENTRY = ServerEntry(
    server_id="server-a",
    host="10.0.1.10",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-a.pem",
    log_path="/var/log/app/application.log",
    format="default",
)


def test_first_read_sets_offset_to_current_size_without_returning_history():
    client = MagicMock()

    def exec_command(command, timeout=None):
        stdout = MagicMock()
        assert "stat -c%s" in command
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"100\n"
        return (MagicMock(), stdout, MagicMock())

    client.exec_command.side_effect = exec_command

    with patch("watcher.ssh_tail.paramiko.SSHClient", return_value=client):
        tailer = SSHTailer(ENTRY)
        result = tailer.read_new_bytes()

    assert result == ""
    assert tailer._offset == 100


def test_second_read_returns_only_new_bytes():
    call_count = {"n": 0}
    client = MagicMock()

    def exec_command(command, timeout=None):
        stdout = MagicMock()
        if "stat -c%s" in command:
            call_count["n"] += 1
            size = b"100\n" if call_count["n"] == 1 else b"120\n"
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = size
        elif "tail -c" in command:
            assert command.strip().startswith("tail -c +101")
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = b"new error line\n"
        return (MagicMock(), stdout, MagicMock())

    client.exec_command.side_effect = exec_command

    with patch("watcher.ssh_tail.paramiko.SSHClient", return_value=client):
        tailer = SSHTailer(ENTRY)
        first = tailer.read_new_bytes()
        second = tailer.read_new_bytes()

    assert first == ""
    assert second == "new error line\n"
    assert tailer._offset == 120


def test_connection_failure_returns_empty_and_retries_next_call():
    with patch("watcher.ssh_tail.paramiko.SSHClient") as mock_ssh_client_cls:
        mock_ssh_client_cls.return_value.connect.side_effect = OSError("unreachable")
        tailer = SSHTailer(ENTRY)
        result = tailer.read_new_bytes()

    assert result == ""
    assert tailer._client is None


def test_rotation_resets_offset_and_rereads_from_start():
    call_count = {"n": 0}
    client = MagicMock()

    def exec_command(command, timeout=None):
        stdout = MagicMock()
        if "stat -c%s" in command:
            call_count["n"] += 1
            size = b"100\n" if call_count["n"] == 1 else b"20\n"
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = size
        elif "tail -c" in command:
            assert command.strip().startswith("tail -c +1 ")
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = b"after rotation\n"
        return (MagicMock(), stdout, MagicMock())

    client.exec_command.side_effect = exec_command

    with patch("watcher.ssh_tail.paramiko.SSHClient", return_value=client):
        tailer = SSHTailer(ENTRY)
        tailer.read_new_bytes()
        result = tailer.read_new_bytes()

    assert result == "after rotation\n"
    assert tailer._offset == 20
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_ssh_tail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watcher.ssh_tail'`

- [ ] **Step 3: `watcher/ssh_tail.py` 구현**

```python
import paramiko


class SSHTailer:
    def __init__(self, entry):
        self.entry = entry
        self._client = None
        self._offset = None

    def read_new_bytes(self):
        if self._client is None:
            if not self._connect():
                return ""

        size = self._remote_size()
        if size is None:
            self._disconnect()
            return ""

        if self._offset is None:
            self._offset = size
            return ""

        if size < self._offset:
            self._offset = 0

        if size == self._offset:
            return ""

        text = self._remote_tail_from(self._offset)
        if text is None:
            self._disconnect()
            return ""

        self._offset = size
        return text

    def _connect(self):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.entry.host,
                port=self.entry.port,
                username=self.entry.username,
                key_filename=self.entry.ssh_key_path,
                timeout=5,
            )
            self._client = client
            return True
        except (paramiko.SSHException, OSError):
            self._client = None
            return False

    def _disconnect(self):
        if self._client is not None:
            self._client.close()
        self._client = None
        self._offset = None

    def _run_command(self, command):
        try:
            _, stdout, _stderr = self._client.exec_command(command, timeout=5)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                return None
            return stdout.read()
        except (paramiko.SSHException, OSError):
            return None

    def _remote_size(self):
        output = self._run_command(f"stat -c%s {self.entry.log_path}")
        if output is None:
            return None
        try:
            return int(output.decode().strip())
        except ValueError:
            return None

    def _remote_tail_from(self, offset):
        output = self._run_command(f"tail -c +{offset + 1} {self.entry.log_path}")
        if output is None:
            return None
        return output.decode(errors="replace")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/watcher/test_ssh_tail.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add watcher/ssh_tail.py tests/watcher/test_ssh_tail.py
git commit -m "feat: add SSH-based remote log tailer with persistent connection"
```

---

### Task 4: 다중 서버 큐 지원으로 `sender.py` 수정

**Files:**
- Modify: `watcher/sender.py` (전체 교체)
- Modify: `tests/watcher/test_sender.py` (전체 교체)

**Interfaces:**
- Consumes: `WatcherConfig`, `ErrorEvent` (Task 1)
- Produces:
  - `DeliveryResult` enum: `DELIVERED`, `RETRY`, `FAILED` (무변경)
  - `next_retry_interval(...)` (무변경)
  - `EventSender(config: WatcherConfig)` — `.send(event) -> bool`, `.flush_queue() -> bool` (이제 `config.queue_dir` 안의 모든 `*.jsonl` 파일을 순회), `.run_retry_loop(...)` (무변경)

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/watcher/test_sender.py` 전체 교체**

```python
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


def test_next_retry_interval_doubles_up_to_cap():
    assert next_retry_interval(30, True) == 60
    assert next_retry_interval(240, True) == 300


def test_next_retry_interval_resets_when_queue_empty():
    assert next_retry_interval(240, False) == 30
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_sender.py -v`
Expected: FAIL — `AttributeError: 'WatcherConfig' object has no attribute 'server_id'` (기존 `EventSender.__init__`이 `config.server_id`를 참조하는 옛 `ServerConfig` 가정으로 작성되어 있어 `WatcherConfig`에는 없는 속성에 접근함)

- [ ] **Step 3: `watcher/sender.py` 전체 교체**

```python
import glob
import json
import os
import threading
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
    def __init__(self, config):
        self.config = config
        os.makedirs(config.queue_dir, exist_ok=True)
        self._queue_lock = threading.Lock()
        if not os.environ.get(config.api_key_env):
            raise ValueError(
                f"Environment variable {config.api_key_env!r} is not set (required for API key)"
            )

    def send(self, event):
        result = self._post_event(event.to_dict())
        if result == DeliveryResult.RETRY:
            self._enqueue(event.server_id, event.to_dict())
        return result == DeliveryResult.DELIVERED

    def flush_queue(self):
        has_remaining = False
        for queue_path in sorted(glob.glob(os.path.join(self.config.queue_dir, "*.jsonl"))):
            if self._flush_one_queue_file(queue_path):
                has_remaining = True
        return has_remaining

    def _flush_one_queue_file(self, queue_path):
        with self._queue_lock:
            with open(queue_path, "r", encoding="utf-8") as f:
                lines = [line for line in f.read().splitlines() if line]

            remaining = []
            for line in lines:
                event_dict = json.loads(line)
                if self._post_event(event_dict) == DeliveryResult.RETRY:
                    remaining.append(line)

            with open(queue_path, "w", encoding="utf-8") as f:
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
                self.config.analyzer_endpoint,
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

    def _enqueue(self, server_id, event_dict):
        queue_path = os.path.join(self.config.queue_dir, f"{server_id}.jsonl")
        with self._queue_lock:
            with open(queue_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_dict) + "\n")
```

- [ ] **Step 4: 이 태스크 범위 테스트 통과 확인**

Run: `pytest tests/watcher/test_sender.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add watcher/sender.py tests/watcher/test_sender.py
git commit -m "refactor: support one queue file per server_id in EventSender"
```

---

### Task 5: `main.py`를 `WatcherManager`로 재작성

**Files:**
- Modify: `watcher/main.py` (전체 교체)
- Modify: `tests/watcher/test_main.py` (전체 교체)

**Interfaces:**
- Consumes: `load_watcher_config` (Task 1), `fetch_servers` (Task 2), `SSHTailer` (Task 3), `EventSender` (Task 4), `LogParser`/`ErrorEventAccumulator` (무변경)
- Produces:
  - `WatcherManager(config, sender)` — `.sync_registry()`, `.poll_once()`, `.run(stop_event)`
  - `run(config_path)` — 전체 프로세스 진입점

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/watcher/test_main.py` 전체 교체**

```python
from unittest.mock import MagicMock, patch

from watcher.main import WatcherManager
from watcher.models import ServerEntry, WatcherConfig

CONFIG = WatcherConfig(
    registry_url="http://dashboard/api/servers",
    analyzer_endpoint="https://collector.example.com/api/errors",
    api_key_env="WATCHER_API_KEY",
    registry_poll_interval=30,
    log_poll_interval=15,
)

ENTRY_A = ServerEntry(
    server_id="server-a",
    host="10.0.1.10",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-a.pem",
    log_path="/var/log/app/application.log",
    format="default",
)

ENTRY_B = ServerEntry(
    server_id="server-b",
    host="10.0.1.11",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-b.pem",
    log_path="/var/log/app/application.log",
    format="default",
)


def test_sync_registry_adds_new_servers():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    assert "server-a" in manager._active


def test_sync_registry_removes_deregistered_servers():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A, ENTRY_B], [])):
        manager.sync_registry()
    assert set(manager._active.keys()) == {"server-a", "server-b"}

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    assert set(manager._active.keys()) == {"server-a"}


def test_sync_registry_keeps_existing_tailer_for_still_registered_server():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()
    tailer_before, _ = manager._active["server-a"]

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()
    tailer_after, _ = manager._active["server-a"]

    assert tailer_before is tailer_after


def test_sync_registry_unreachable_keeps_last_known_list():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    with patch("watcher.main.fetch_servers", side_effect=Exception("registry down")):
        manager.sync_registry()

    assert "server-a" in manager._active


def test_poll_once_feeds_lines_and_sends_completed_events():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    tailer, _accumulator = manager._active["server-a"]
    tailer.read_new_bytes = MagicMock(
        return_value=(
            "2026-08-06 12:35:01.123 ERROR 12345 --- [main] com.example.demo.MyService : boom\n"
            "java.lang.RuntimeException: boom\n"
            "2026-08-06 12:35:05.001  INFO 12345 --- [main] com.example.demo.App : Recovered"
        )
    )

    manager.poll_once()

    sender.send.assert_called_once()
    sent_event = sender.send.call_args[0][0]
    assert sent_event.error_type == "java.lang.RuntimeException"


def test_poll_once_skips_server_with_no_new_bytes():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    tailer, _accumulator = manager._active["server-a"]
    tailer.read_new_bytes = MagicMock(return_value="")

    manager.poll_once()

    sender.send.assert_not_called()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/watcher/test_main.py -v`
Expected: FAIL — `ImportError: cannot import name 'WatcherManager' from 'watcher.main'` (옛 `main.py`는 `LogFileHandler`만 정의)

- [ ] **Step 3: `watcher/main.py` 전체 교체**

```python
import argparse
import threading
import time

from watcher.config import load_watcher_config
from watcher.parser import ErrorEventAccumulator, LogParser
from watcher.registry_client import fetch_servers
from watcher.sender import EventSender
from watcher.ssh_tail import SSHTailer


class WatcherManager:
    def __init__(self, config, sender):
        self.config = config
        self.sender = sender
        self._active = {}
        self._lock = threading.Lock()

    def sync_registry(self):
        try:
            servers, _skipped = fetch_servers(self.config.registry_url)
        except Exception:
            return

        current_ids = {entry.server_id for entry in servers}

        with self._lock:
            for server_id in list(self._active.keys()):
                if server_id not in current_ids:
                    del self._active[server_id]

            for entry in servers:
                if entry.server_id not in self._active:
                    parser = LogParser(entry)
                    accumulator = ErrorEventAccumulator(parser)
                    tailer = SSHTailer(entry)
                    self._active[entry.server_id] = (tailer, accumulator)

    def poll_once(self):
        with self._lock:
            items = list(self._active.values())

        for tailer, accumulator in items:
            text = tailer.read_new_bytes()
            if not text:
                continue
            for line in text.splitlines():
                completed_event = accumulator.feed_line(line)
                if completed_event is not None:
                    self.sender.send(completed_event)

    def run(self, stop_event):
        def registry_loop():
            while not stop_event.is_set():
                self.sync_registry()
                stop_event.wait(self.config.registry_poll_interval)

        def poll_loop():
            while not stop_event.is_set():
                self.poll_once()
                stop_event.wait(self.config.log_poll_interval)

        registry_thread = threading.Thread(target=registry_loop, daemon=True)
        poll_thread = threading.Thread(target=poll_loop, daemon=True)
        retry_thread = threading.Thread(
            target=self.sender.run_retry_loop, kwargs={"stop_event": stop_event}, daemon=True
        )

        registry_thread.start()
        poll_thread.start()
        retry_thread.start()

        try:
            while not stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            stop_event.set()


def run(config_path):
    config = load_watcher_config(config_path)
    sender = EventSender(config)
    manager = WatcherManager(config, sender)
    manager.sync_registry()
    stop_event = threading.Event()
    manager.run(stop_event)


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Run the central SSH-based log watcher.")
    argparser.add_argument("config_path", help="Path to the watcher's YAML config file")
    args = argparser.parse_args()
    run(args.config_path)
```

- [ ] **Step 4: 이 태스크 범위 테스트 통과 확인**

Run: `pytest tests/watcher/test_main.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 전체 스위트 실행 (마이그레이션 완료 확인)**

Run: `pytest -v`
Expected: 모든 테스트(Task 1~5, 총 30개: config 3 + parser 5 + registry_client 4 + ssh_tail 4 + sender 8 + main 6) PASS. 옛 `watcher/config.py`의 `ServerConfig` 관련 잔재나 `watchdog` import가 전혀 남아있지 않은지 `grep -rn "ServerConfig\|watchdog" watcher/` 로 확인 (결과 없어야 함).

- [ ] **Step 6: Commit**

```bash
git add watcher/main.py tests/watcher/test_main.py
git commit -m "refactor: rewrite main.py as WatcherManager coordinating registry sync and SSH polling"
```

---

### Task 6: 메모리 문서 갱신 (레지스트리 API 계약 + 전송 방식 설명 수정)

**Files:**
- Create: `.claude/memory/log-watcher/server-registry-api-contract.md`
- Modify: `.claude/memory/log-watcher/error-event-schema.md` (전송 방식 절만 수정)

**Interfaces:**
- Consumes: `ServerEntry` 필드 정의 (Task 1) — dashboard 하위 프로젝트가 구현할 계약

- [ ] **Step 1: 레지스트리 API 계약 문서 작성**

`.claude/memory/log-watcher/server-registry-api-contract.md`:
```markdown
# 서버 레지스트리 API 계약 (dashboard가 구현, watcher가 호출)

- 작성일: 2026-08-06
- 관련 설계 문서: docs/superpowers/specs/2026-08-06-log-watcher-ssh-redesign.md
- 관련 구현: watcher/registry_client.py의 fetch_servers()

## 결론 요약
watcher는 30초(기본값)마다 아래 API를 호출해 현재 감시해야 할 서버 목록을 가져온다.
dashboard는 이 스키마로 응답하는 GET 엔드포인트를 구현해야 한다.

## API

```
GET {registry_url} → 200 OK
Content-Type: application/json

[
  {
    "server_id": "server-a",
    "host": "10.0.1.10",
    "port": 22,
    "username": "deploy",
    "ssh_key_path": "/home/watcher/.ssh/server-a.pem",
    "log_path": "/var/log/app/application.log",
    "format": "default",
    "custom_pattern": null
  }
]
```

## 필드 설명
- `format`: `"default"`(표준 logback 패턴) 또는 `"custom"`(정규식 사용)
- `custom_pattern`: `format`이 `"custom"`일 때만 필수. named group으로 `timestamp`/`level`/`message`를 최소 포함해야 한다.
- `ssh_key_path`: watcher가 실행되는 중앙 서버 파일시스템 상의 개인키 경로. 키 파일 자체는 이 API로 전달되지 않는다 — dashboard는 경로 문자열만 저장/반환한다.

## watcher 쪽 동작
- 이 목록에 새로 나타난 `server_id`는 자동으로 SSH 감시가 시작된다.
- 이 목록에서 사라진 `server_id`는 자동으로 감시가 중단된다.
- 개별 항목이 유효하지 않으면(정규식 오류, 필수 필드 누락 등) 그 항목만 건너뛰고 나머지는 정상 처리한다.
- 이 API 자체가 응답하지 않으면 watcher는 마지막으로 알고 있던 목록을 유지하고 다음 주기에 재시도한다 (전체 프로세스가 멈추지 않는다).
```

- [ ] **Step 2: `error-event-schema.md`의 전송 방식 절 수정**

기존 27번째 줄:
```
- 헤더: `X-API-Key: <서버별 설정의 api_key_env가 가리키는 환경변수 값>`
```
다음으로 교체:
```
- 헤더: `X-API-Key: <watcher 전체가 공유하는 WatcherConfig.api_key_env가 가리키는 환경변수 값>` (2026-08-06 SSH 재설계 이후 서버별이 아닌 watcher 프로세스 전체가 공유하는 하나의 키다)
```

- [ ] **Step 3: Commit**

```bash
git add .claude/memory/log-watcher/server-registry-api-contract.md .claude/memory/log-watcher/error-event-schema.md
git commit -m "docs: record server registry API contract and update shared API key note"
```
