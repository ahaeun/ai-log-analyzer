# dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `dashboard/` FastAPI app — SQLite-backed server registry + error history storage, a machine-to-machine API for `watcher`/`analyzer`, Slack-login-gated pages, and a TailAdmin-styled home dashboard.

**Architecture:** `dashboard/db.py` owns all SQLite access (plain `sqlite3`, no ORM) behind plain functions taking `db_path` as an argument for testability. `dashboard/validation.py` reuses `watcher.models.ServerEntry`'s validation so server-registration rules never drift from what `watcher` actually enforces. `dashboard/auth.py` implements Slack's OpenID Connect ("Sign in with Slack") flow and the workspace/email allowlist check; sessions are a signed cookie via Starlette's `SessionMiddleware` — no server-side session store. Routes are split by concern into `dashboard/routes/` (api, auth, servers, errors, home) and mounted onto one `FastAPI()` app in `dashboard/main.py`. Pages are server-rendered Jinja2 templates styled with Tailwind CSS and Chart.js, both loaded from CDN — no frontend build step.

**Tech Stack:** Python 3, FastAPI, Uvicorn, Jinja2, Starlette `SessionMiddleware` (itsdangerous), `python-multipart` (HTML form parsing), `requests` (Slack API calls), `httpx` (required by FastAPI's `TestClient`), `pytest`, SQLite (stdlib `sqlite3`), Tailwind CSS + Chart.js via CDN.

## Global Constraints

- 담당 범위는 `dashboard/`와 그 테스트(`tests/dashboard/`)로 한정한다. `watcher/`는 읽기 전용으로 import만 하고 수정하지 않는다.
- API 키(`DASHBOARD_API_KEY`)와 Slack 클라이언트 시크릿은 하드코딩하지 않고 환경변수에서만 로드한다.
- `dashboard` 자체의 설정(환경변수) 누락은 즉시 `ValueError`로 실패시킨다 — 조용히 무시하지 않는다.
- Slack 로그인은 `SLACK_TEAM_ID`와 일치하는 워크스페이스이면서 `DASHBOARD_ALLOWED_EMAILS`에 있는 이메일만 허용한다. 둘 중 하나라도 불일치하면 403.
- `errors` 테이블의 `server_id`는 `servers` 테이블에 대한 FK 제약을 걸지 않는다 — 서버 삭제 후에도 에러 이력은 보존한다.
- 서버 등록/수정 시 `custom_pattern` 검증은 `watcher.models.ServerEntry`의 검증 로직(정규식 컴파일 가능 여부 + `timestamp`/`level`/`message` named group 필수)을 그대로 재사용한다 — 별도로 재구현하지 않는다.
- `POST /api/errors`, `GET /api/servers`는 `X-API-Key` 헤더가 `DASHBOARD_API_KEY`와 일치해야 한다. 불일치/누락 시 401.

---

### Task 1: 프로젝트 스캐폴딩 + 설정 로더

**Files:**
- Modify: `requirements.txt`
- Create: `dashboard/__init__.py`
- Create: `dashboard/config.py`
- Create: `tests/dashboard/__init__.py`
- Test: `tests/dashboard/test_config.py`

**Interfaces:**
- Produces: `DashboardConfig(db_path, slack_client_id, slack_client_secret, slack_team_id, allowed_emails: list[str], session_secret, api_key)`, `load_config_from_env() -> DashboardConfig` (raises `ValueError` if any required env var is missing)

- [ ] **Step 1: `requirements.txt`에 추가**

```
fastapi>=0.110.0
uvicorn>=0.29.0
itsdangerous>=2.1.0
python-multipart>=0.0.9
jinja2>=3.1.0
httpx>=0.27.0
```

(기존 `watchdog`/`paramiko`/`requests`/`PyYAML`/`pytest` 줄은 그대로 둔다.)

Run: `python3 -m pip install -r requirements.txt`
Expected: 설치 성공

- [ ] **Step 2: 디렉터리 생성**

`dashboard/__init__.py`: (빈 파일)
`tests/dashboard/__init__.py`: (빈 파일)

- [ ] **Step 3: 실패하는 테스트 작성**

`tests/dashboard/test_config.py`:
```python
import os

import pytest

from dashboard.config import load_config_from_env

REQUIRED_ENV = {
    "SLACK_CLIENT_ID": "client-id",
    "SLACK_CLIENT_SECRET": "client-secret",
    "SLACK_TEAM_ID": "T12345",
    "DASHBOARD_ALLOWED_EMAILS": "a@example.com, b@example.com",
    "DASHBOARD_SESSION_SECRET": "session-secret",
    "DASHBOARD_API_KEY": "api-key",
}


@pytest.fixture
def set_required_env(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_load_config_from_env(set_required_env, monkeypatch):
    monkeypatch.setenv("DASHBOARD_DB_PATH", "/tmp/dashboard-test.db")

    config = load_config_from_env()

    assert config.db_path == "/tmp/dashboard-test.db"
    assert config.slack_client_id == "client-id"
    assert config.slack_client_secret == "client-secret"
    assert config.slack_team_id == "T12345"
    assert config.allowed_emails == ["a@example.com", "b@example.com"]
    assert config.session_secret == "session-secret"
    assert config.api_key == "api-key"


def test_db_path_defaults_when_not_set(set_required_env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_DB_PATH", raising=False)

    config = load_config_from_env()

    assert config.db_path == "dashboard/data.db"


def test_missing_required_env_var_raises(set_required_env, monkeypatch):
    monkeypatch.delenv("SLACK_CLIENT_ID", raising=False)

    with pytest.raises(ValueError, match="SLACK_CLIENT_ID"):
        load_config_from_env()
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.config'`

- [ ] **Step 5: `dashboard/config.py` 구현**

```python
import os
from dataclasses import dataclass
from typing import List

REQUIRED_ENV_VARS = (
    "SLACK_CLIENT_ID",
    "SLACK_CLIENT_SECRET",
    "SLACK_TEAM_ID",
    "DASHBOARD_ALLOWED_EMAILS",
    "DASHBOARD_SESSION_SECRET",
    "DASHBOARD_API_KEY",
)


@dataclass
class DashboardConfig:
    db_path: str
    slack_client_id: str
    slack_client_secret: str
    slack_team_id: str
    allowed_emails: List[str]
    session_secret: str
    api_key: str


def load_config_from_env() -> DashboardConfig:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")

    allowed_emails = [
        email.strip()
        for email in os.environ["DASHBOARD_ALLOWED_EMAILS"].split(",")
        if email.strip()
    ]

    return DashboardConfig(
        db_path=os.environ.get("DASHBOARD_DB_PATH", "dashboard/data.db"),
        slack_client_id=os.environ["SLACK_CLIENT_ID"],
        slack_client_secret=os.environ["SLACK_CLIENT_SECRET"],
        slack_team_id=os.environ["SLACK_TEAM_ID"],
        allowed_emails=allowed_emails,
        session_secret=os.environ["DASHBOARD_SESSION_SECRET"],
        api_key=os.environ["DASHBOARD_API_KEY"],
    )
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt dashboard/__init__.py dashboard/config.py tests/dashboard/__init__.py tests/dashboard/test_config.py
git commit -m "chore: dashboard 패키지 스캐폴딩과 설정 로더 추가"
```

---

### Task 2: SQLite 데이터 계층 (`db.py`)

**Files:**
- Create: `dashboard/db.py`
- Test: `tests/dashboard/test_db.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `init_db(db_path)`
  - `insert_server(db_path, server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern)`
  - `update_server(db_path, server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern)`
  - `delete_server(db_path, server_id)`
  - `get_server(db_path, server_id) -> dict | None`
  - `list_servers(db_path) -> list[dict]`
  - `insert_error(db_path, server_id, timestamp, log_level, error_type, message, stack_trace, raw_log, ai_analysis=None, notified=False, notified_at=None)`
  - `query_errors(db_path, server_id=None, date_from=None, date_to=None, error_type=None, limit=50, offset=0) -> list[dict]`
  - `count_servers(db_path) -> int`
  - `count_errors_since(db_path, since_iso) -> int`
  - `count_notified_since(db_path, since_iso) -> int`
  - `error_counts_by_day(db_path, since_iso) -> list[dict]` (각 항목 `{"day": "YYYY-MM-DD", "count": int}`)
  - `recent_errors(db_path, limit=5) -> list[dict]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/dashboard/test_db.py`:
```python
import pytest

from dashboard import db


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_db(path)
    return path


def test_insert_and_get_server(db_path):
    db.insert_server(
        db_path, "server-a", "10.0.1.10", 22, "deploy",
        "/home/w/.ssh/a.pem", "/var/log/app/app.log", "default", None,
    )

    server = db.get_server(db_path, "server-a")

    assert server["server_id"] == "server-a"
    assert server["host"] == "10.0.1.10"
    assert server["port"] == 22
    assert server["format"] == "default"
    assert server["custom_pattern"] is None
    assert server["created_at"]
    assert server["updated_at"]


def test_get_server_returns_none_when_missing(db_path):
    assert db.get_server(db_path, "does-not-exist") is None


def test_list_servers_ordered_by_id(db_path):
    db.insert_server(db_path, "server-b", "h", 22, "u", "k", "l", "default", None)
    db.insert_server(db_path, "server-a", "h", 22, "u", "k", "l", "default", None)

    servers = db.list_servers(db_path)

    assert [s["server_id"] for s in servers] == ["server-a", "server-b"]


def test_update_server_changes_fields_and_updated_at(db_path):
    db.insert_server(db_path, "server-a", "10.0.1.10", 22, "deploy", "k", "l", "default", None)
    original = db.get_server(db_path, "server-a")

    db.update_server(
        db_path, "server-a", "10.0.1.99", 2222, "ops",
        "/new/key.pem", "/var/log/new.log", "custom",
        r"^(?P<timestamp>\S+) (?P<level>\w+) (?P<message>.*)$",
    )
    updated = db.get_server(db_path, "server-a")

    assert updated["host"] == "10.0.1.99"
    assert updated["port"] == 2222
    assert updated["format"] == "custom"
    assert updated["updated_at"] != original["updated_at"]


def test_delete_server_removes_it(db_path):
    db.insert_server(db_path, "server-a", "h", 22, "u", "k", "l", "default", None)

    db.delete_server(db_path, "server-a")

    assert db.get_server(db_path, "server-a") is None


def _insert_sample_error(db_path, server_id="server-a", timestamp="2026-08-06T10:00:00+09:00",
                          error_type="java.lang.NullPointerException", notified=False, notified_at=None):
    db.insert_error(
        db_path, server_id, timestamp, "ERROR", error_type,
        "boom", "at ...", "raw log line",
        ai_analysis="원인: ...", notified=notified, notified_at=notified_at,
    )


def test_insert_error_and_query_all(db_path):
    _insert_sample_error(db_path)

    results = db.query_errors(db_path)

    assert len(results) == 1
    assert results[0]["server_id"] == "server-a"
    assert results[0]["error_type"] == "java.lang.NullPointerException"
    assert results[0]["ai_analysis"] == "원인: ..."
    assert results[0]["notified"] == 0
    assert results[0]["received_at"]


def test_query_errors_filters_by_server_id(db_path):
    _insert_sample_error(db_path, server_id="server-a")
    _insert_sample_error(db_path, server_id="server-b")

    results = db.query_errors(db_path, server_id="server-b")

    assert len(results) == 1
    assert results[0]["server_id"] == "server-b"


def test_query_errors_filters_by_date_range(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-01T10:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-10T10:00:00+09:00")

    results = db.query_errors(db_path, date_from="2026-08-05T00:00:00+09:00")

    assert len(results) == 1
    assert results[0]["timestamp"] == "2026-08-10T10:00:00+09:00"


def test_query_errors_filters_by_error_type_substring(db_path):
    _insert_sample_error(db_path, error_type="java.lang.NullPointerException")
    _insert_sample_error(db_path, error_type="java.lang.RuntimeException")

    results = db.query_errors(db_path, error_type="Null")

    assert len(results) == 1
    assert results[0]["error_type"] == "java.lang.NullPointerException"


def test_query_errors_respects_limit_and_offset(db_path):
    for i in range(3):
        _insert_sample_error(db_path, timestamp=f"2026-08-0{i+1}T10:00:00+09:00")

    page = db.query_errors(db_path, limit=1, offset=1)

    assert len(page) == 1
    assert page[0]["timestamp"] == "2026-08-02T10:00:00+09:00"


def test_count_servers(db_path):
    db.insert_server(db_path, "server-a", "h", 22, "u", "k", "l", "default", None)
    db.insert_server(db_path, "server-b", "h", 22, "u", "k", "l", "default", None)

    assert db.count_servers(db_path) == 2


def test_count_errors_since(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-01T10:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-10T10:00:00+09:00")

    assert db.count_errors_since(db_path, "2026-08-05T00:00:00+09:00") == 1


def test_count_notified_since(db_path):
    _insert_sample_error(db_path, notified=True, notified_at="2026-08-10T10:00:05+09:00")
    _insert_sample_error(db_path, notified=False)

    assert db.count_notified_since(db_path, "2026-08-05T00:00:00+09:00") == 1


def test_error_counts_by_day_groups_correctly(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-06T09:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-06T15:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-07T09:00:00+09:00")

    counts = db.error_counts_by_day(db_path, "2026-08-01T00:00:00+09:00")

    assert counts == [{"day": "2026-08-06", "count": 2}, {"day": "2026-08-07", "count": 1}]


def test_recent_errors_orders_newest_first_and_limits(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-01T10:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-10T10:00:00+09:00")

    results = db.recent_errors(db_path, limit=1)

    assert len(results) == 1
    assert results[0]["timestamp"] == "2026-08-10T10:00:00+09:00"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.db'`

- [ ] **Step 3: `dashboard/db.py` 구현**

```python
import sqlite3
from datetime import datetime, timezone


def get_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS servers (
                server_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                username TEXT NOT NULL,
                ssh_key_path TEXT NOT NULL,
                log_path TEXT NOT NULL,
                format TEXT NOT NULL CHECK (format IN ('default', 'custom')),
                custom_pattern TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                log_level TEXT NOT NULL,
                error_type TEXT NOT NULL,
                message TEXT NOT NULL,
                stack_trace TEXT NOT NULL,
                raw_log TEXT NOT NULL,
                ai_analysis TEXT,
                notified INTEGER NOT NULL DEFAULT 0,
                notified_at TEXT,
                received_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def list_servers(db_path):
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM servers ORDER BY server_id").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_server(db_path, server_id):
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE server_id = ?", (server_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_server(db_path, server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern):
    now = _now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO servers
               (server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_server(db_path, server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern):
    conn = get_connection(db_path)
    try:
        conn.execute(
            """UPDATE servers SET host=?, port=?, username=?, ssh_key_path=?, log_path=?,
               format=?, custom_pattern=?, updated_at=? WHERE server_id=?""",
            (host, port, username, ssh_key_path, log_path, format, custom_pattern, _now_iso(), server_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_server(db_path, server_id):
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM servers WHERE server_id = ?", (server_id,))
        conn.commit()
    finally:
        conn.close()


def insert_error(db_path, server_id, timestamp, log_level, error_type, message, stack_trace, raw_log,
                  ai_analysis=None, notified=False, notified_at=None):
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO errors
               (server_id, timestamp, log_level, error_type, message, stack_trace, raw_log,
                ai_analysis, notified, notified_at, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (server_id, timestamp, log_level, error_type, message, stack_trace, raw_log,
             ai_analysis, 1 if notified else 0, notified_at, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def query_errors(db_path, server_id=None, date_from=None, date_to=None, error_type=None, limit=50, offset=0):
    conditions = []
    params = []
    if server_id:
        conditions.append("server_id = ?")
        params.append(server_id)
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to)
    if error_type:
        conditions.append("error_type LIKE ?")
        params.append(f"%{error_type}%")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM errors {where_clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_servers(db_path):
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM servers").fetchone()["c"]
    finally:
        conn.close()


def count_errors_since(db_path, since_iso):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE timestamp >= ?", (since_iso,)
        ).fetchone()["c"]
    finally:
        conn.close()


def count_notified_since(db_path, since_iso):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE notified = 1 AND notified_at >= ?", (since_iso,)
        ).fetchone()["c"]
    finally:
        conn.close()


def error_counts_by_day(db_path, since_iso):
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS c
               FROM errors WHERE timestamp >= ? GROUP BY day ORDER BY day""",
            (since_iso,),
        ).fetchall()
        return [{"day": row["day"], "count": row["c"]} for row in rows]
    finally:
        conn.close()


def recent_errors(db_path, limit=5):
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM errors ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_db.py -v`
Expected: PASS (16 passed)

- [ ] **Step 5: Commit**

```bash
git add dashboard/db.py tests/dashboard/test_db.py
git commit -m "feat: SQLite 서버/에러 데이터 계층 추가"
```

---

### Task 3: 서버 등록 검증 재사용 (`validation.py`) + 기계 간 API (`routes/api.py`)

**Files:**
- Create: `dashboard/validation.py`
- Create: `dashboard/routes/__init__.py`
- Create: `dashboard/routes/api.py`
- Test: `tests/dashboard/test_validation.py`
- Test: `tests/dashboard/test_api_routes.py`

**Interfaces:**
- Consumes: `watcher.models.ServerEntry` (읽기 전용 import), `dashboard.db` (Task 2), `dashboard.config.DashboardConfig` (Task 1)
- Produces:
  - `ServerValidationError(ValueError)`, `validate_server_fields(server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern)` — 유효하지 않으면 `ServerValidationError` 발생
  - `api_router: fastapi.APIRouter` — `GET /api/servers`, `POST /api/errors` (둘 다 `X-API-Key` 검증)

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/dashboard/test_validation.py`**

```python
import pytest

from dashboard.validation import ServerValidationError, validate_server_fields


def test_valid_default_format_passes():
    validate_server_fields(
        "server-a", "10.0.1.10", 22, "deploy",
        "/home/w/.ssh/a.pem", "/var/log/app.log", "default", None,
    )


def test_valid_custom_format_passes():
    validate_server_fields(
        "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
        "custom", r"^(?P<timestamp>\S+) (?P<level>\w+) (?P<message>.*)$",
    )


def test_custom_format_without_pattern_raises():
    with pytest.raises(ServerValidationError, match="custom_pattern is required"):
        validate_server_fields(
            "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
            "custom", None,
        )


def test_custom_format_invalid_regex_raises():
    with pytest.raises(ServerValidationError, match="invalid custom_pattern regex"):
        validate_server_fields(
            "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
            "custom", "(?P<timestamp>[",
        )


def test_custom_format_missing_named_groups_raises():
    with pytest.raises(ServerValidationError, match="named groups"):
        validate_server_fields(
            "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
            "custom", r"(?P<message>.*)",
        )
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.validation'`

- [ ] **Step 3: `dashboard/validation.py` 구현**

```python
from watcher.models import ServerEntry


class ServerValidationError(ValueError):
    pass


def validate_server_fields(server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern):
    try:
        ServerEntry(
            server_id=server_id,
            host=host,
            port=port,
            username=username,
            ssh_key_path=ssh_key_path,
            log_path=log_path,
            format=format,
            custom_pattern=custom_pattern,
        )
    except ValueError as e:
        raise ServerValidationError(str(e)) from e
```

- [ ] **Step 4: 검증 테스트 통과 확인**

Run: `pytest tests/dashboard/test_validation.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 실패하는 테스트 작성 — `tests/dashboard/test_api_routes.py`**

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import db
from dashboard.config import DashboardConfig
from dashboard.routes.api import api_router

CONFIG = DashboardConfig(
    db_path="",  # overwritten per test
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="test-api-key",
)


@pytest.fixture
def client(tmp_path):
    config = DashboardConfig(**{**CONFIG.__dict__, "db_path": str(tmp_path / "test.db")})
    db.init_db(config.db_path)

    app = FastAPI()
    app.state.config = config
    app.include_router(api_router)
    return TestClient(app)


def test_get_servers_requires_api_key(client):
    response = client.get("/api/servers")
    assert response.status_code == 401


def test_get_servers_returns_registered_servers(client):
    config = client.app.state.config
    db.insert_server(config.db_path, "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log", "default", None)

    response = client.get("/api/servers", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["server_id"] == "server-a"
    assert body[0]["host"] == "10.0.1.10"


def test_post_errors_requires_api_key(client):
    response = client.post("/api/errors", json={})
    assert response.status_code == 401


def test_post_errors_stores_error(client):
    payload = {
        "server_id": "server-a",
        "timestamp": "2026-08-06T12:35:01+09:00",
        "log_level": "ERROR",
        "error_type": "java.lang.NullPointerException",
        "message": "boom",
        "stack_trace": "at ...",
        "raw_log": "raw",
        "ai_analysis": "원인: ...",
        "notified": True,
        "notified_at": "2026-08-06T12:35:05+09:00",
    }

    response = client.post("/api/errors", json=payload, headers={"X-API-Key": "test-api-key"})

    assert response.status_code == 200
    config = client.app.state.config
    stored = db.query_errors(config.db_path)
    assert len(stored) == 1
    assert stored[0]["error_type"] == "java.lang.NullPointerException"
    assert stored[0]["notified"] == 1


def test_post_errors_missing_required_field_returns_422(client):
    response = client.post(
        "/api/errors",
        json={"server_id": "server-a"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 422
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_api_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.routes'`

- [ ] **Step 7: `dashboard/routes/__init__.py`, `dashboard/routes/api.py` 구현**

`dashboard/routes/__init__.py`: (빈 파일)

`dashboard/routes/api.py`:
```python
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from dashboard import db

api_router = APIRouter()


def _check_api_key(request: Request, x_api_key: Optional[str]):
    config = request.app.state.config
    if not x_api_key or x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


class ErrorIn(BaseModel):
    server_id: str
    timestamp: str
    log_level: str
    error_type: str
    message: str
    stack_trace: str
    raw_log: str
    ai_analysis: Optional[str] = None
    notified: bool = False
    notified_at: Optional[str] = None


@api_router.get("/api/servers")
def get_servers(request: Request, x_api_key: Optional[str] = Header(default=None)):
    _check_api_key(request, x_api_key)
    config = request.app.state.config
    servers = db.list_servers(config.db_path)
    return [
        {
            "server_id": s["server_id"],
            "host": s["host"],
            "port": s["port"],
            "username": s["username"],
            "ssh_key_path": s["ssh_key_path"],
            "log_path": s["log_path"],
            "format": s["format"],
            "custom_pattern": s["custom_pattern"],
        }
        for s in servers
    ]


@api_router.post("/api/errors")
def post_error(request: Request, body: ErrorIn, x_api_key: Optional[str] = Header(default=None)):
    _check_api_key(request, x_api_key)
    config = request.app.state.config
    db.insert_error(
        config.db_path,
        body.server_id, body.timestamp, body.log_level, body.error_type,
        body.message, body.stack_trace, body.raw_log,
        ai_analysis=body.ai_analysis, notified=body.notified, notified_at=body.notified_at,
    )
    return {"status": "ok"}
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_api_routes.py -v`
Expected: PASS (5 passed)

- [ ] **Step 9: Commit**

```bash
git add dashboard/validation.py dashboard/routes/__init__.py dashboard/routes/api.py tests/dashboard/test_validation.py tests/dashboard/test_api_routes.py
git commit -m "feat: 서버 등록 검증 재사용과 기계간 API(/api/servers, /api/errors) 추가"
```

---

### Task 4: Slack 로그인 (`auth.py` + 인증 라우트)

**Files:**
- Create: `dashboard/auth.py`
- Create: `dashboard/templating.py`
- Create: `dashboard/routes/auth_routes.py`
- Create: `dashboard/templates/login.html`
- Test: `tests/dashboard/test_auth.py`
- Test: `tests/dashboard/test_auth_routes.py`

**Interfaces:**
- Consumes: `dashboard.config.DashboardConfig` (Task 1)
- Produces:
  - `generate_state() -> str`
  - `build_authorize_url(config, redirect_uri, state) -> str`
  - `exchange_code_for_token(config, code, redirect_uri) -> str` (access token)
  - `fetch_userinfo(access_token) -> dict` (`{"email": ..., "team_id": ...}`)
  - `is_authorized(userinfo, config) -> bool`
  - `NotAuthenticated(Exception)`
  - `require_session(request) -> dict` (세션의 `user` 딕셔너리 반환, 없으면 `NotAuthenticated` 발생)
  - `templates: fastapi.templating.Jinja2Templates` (directory=`dashboard/templates`)
  - `auth_router: fastapi.APIRouter` — `GET /login`, `GET /login/slack`, `GET /auth/slack/callback` (name=`auth_callback`), `POST /logout`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/dashboard/test_auth.py`**

```python
from unittest.mock import MagicMock, patch

import pytest

from dashboard.auth import (
    build_authorize_url,
    exchange_code_for_token,
    fetch_userinfo,
    generate_state,
    is_authorized,
)
from dashboard.config import DashboardConfig

CONFIG = DashboardConfig(
    db_path="x", slack_client_id="client-123", slack_client_secret="secret-456",
    slack_team_id="T12345", allowed_emails=["a@example.com"],
    session_secret="s", api_key="k",
)


def test_generate_state_returns_nonempty_unique_strings():
    a = generate_state()
    b = generate_state()
    assert a and b and a != b


def test_build_authorize_url_includes_required_params():
    from urllib.parse import parse_qs, urlparse

    url = build_authorize_url(CONFIG, "https://dash.example.com/auth/slack/callback", "state-xyz")

    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://slack.com/openid/connect/authorize"
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["client-123"]
    assert params["state"] == ["state-xyz"]
    assert params["redirect_uri"] == ["https://dash.example.com/auth/slack/callback"]
    assert params["scope"] == ["openid email profile"]


def test_exchange_code_for_token_returns_access_token():
    fake_response = MagicMock()
    fake_response.json.return_value = {"ok": True, "access_token": "token-abc"}
    fake_response.raise_for_status.return_value = None

    with patch("dashboard.auth.requests.post", return_value=fake_response) as mock_post:
        token = exchange_code_for_token(CONFIG, "code-1", "https://dash.example.com/auth/slack/callback")

    assert token == "token-abc"
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["client_id"] == "client-123"
    assert kwargs["data"]["client_secret"] == "secret-456"
    assert kwargs["data"]["code"] == "code-1"


def test_fetch_userinfo_extracts_email_and_team_id():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "email": "a@example.com",
        "https://slack.com/team_id": "T12345",
    }
    fake_response.raise_for_status.return_value = None

    with patch("dashboard.auth.requests.get", return_value=fake_response) as mock_get:
        userinfo = fetch_userinfo("token-abc")

    assert userinfo == {"email": "a@example.com", "team_id": "T12345"}
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token-abc"


def test_is_authorized_true_for_matching_team_and_allowed_email():
    assert is_authorized({"email": "a@example.com", "team_id": "T12345"}, CONFIG) is True


def test_is_authorized_false_for_wrong_team():
    assert is_authorized({"email": "a@example.com", "team_id": "T99999"}, CONFIG) is False


def test_is_authorized_false_for_email_not_in_allowlist():
    assert is_authorized({"email": "stranger@example.com", "team_id": "T12345"}, CONFIG) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.auth'`

- [ ] **Step 3: `dashboard/auth.py` 구현**

```python
import secrets
from urllib.parse import urlencode

import requests
from fastapi import Request

SLACK_AUTHORIZE_URL = "https://slack.com/openid/connect/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/openid.connect.token"
SLACK_USERINFO_URL = "https://slack.com/api/openid.connect.userInfo"


class NotAuthenticated(Exception):
    pass


def generate_state():
    return secrets.token_urlsafe(24)


def build_authorize_url(config, redirect_uri, state):
    params = {
        "client_id": config.slack_client_id,
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(config, code, redirect_uri):
    response = requests.post(
        SLACK_TOKEN_URL,
        data={
            "client_id": config.slack_client_id,
            "client_secret": config.slack_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    return data["access_token"]


def fetch_userinfo(access_token):
    response = requests.get(
        SLACK_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "email": data.get("email"),
        "team_id": data.get("https://slack.com/team_id"),
    }


def is_authorized(userinfo, config):
    if userinfo.get("team_id") != config.slack_team_id:
        return False
    if userinfo.get("email") not in config.allowed_emails:
        return False
    return True


def require_session(request: Request):
    user = request.session.get("user")
    if not user:
        raise NotAuthenticated()
    return user
```

- [ ] **Step 4: 인증 헬퍼 테스트 통과 확인**

Run: `pytest tests/dashboard/test_auth.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: `dashboard/templating.py` 구현**

```python
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="dashboard/templates")
```

- [ ] **Step 6: `dashboard/templates/login.html` 작성**

```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <title>로그인 - 에러 로그 대시보드</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center">
  <div class="bg-white rounded-2xl shadow-sm p-10 w-full max-w-sm text-center">
    <h1 class="text-xl font-semibold text-gray-800 mb-2">에러 로그 대시보드</h1>
    <p class="text-sm text-gray-500 mb-6">팀 Slack 계정으로 로그인하세요.</p>
    {% if error %}
    <p class="text-sm text-red-600 mb-4">{{ error }}</p>
    {% endif %}
    <a href="/login/slack"
       class="block w-full bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-xl py-2.5 transition">
      Slack으로 로그인
    </a>
  </div>
</body>
</html>
```

- [ ] **Step 7: 실패하는 테스트 작성 — `tests/dashboard/test_auth_routes.py`**

```python
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.testclient import TestClient

from dashboard.config import DashboardConfig
from dashboard.routes.auth_routes import auth_router

CONFIG = DashboardConfig(
    db_path="x", slack_client_id="client-123", slack_client_secret="secret-456",
    slack_team_id="T12345", allowed_emails=["a@example.com"],
    session_secret="test-session-secret", api_key="k",
)


@pytest.fixture
def client():
    app = FastAPI()
    app.state.config = CONFIG
    app.add_middleware(SessionMiddleware, secret_key=CONFIG.session_secret)
    app.include_router(auth_router)
    return TestClient(app, follow_redirects=False)


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Slack" in response.text


def test_login_slack_redirects_to_slack_authorize(client):
    response = client.get("/login/slack")
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("https://slack.com/openid/connect/authorize")


def test_callback_rejects_state_mismatch(client):
    client.get("/login/slack")  # sets session["oauth_state"]

    response = client.get("/auth/slack/callback", params={"code": "abc", "state": "wrong"})

    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith("/login")


def test_callback_rejects_unauthorized_user(client):
    login_response = client.get("/login/slack")
    state = login_response.headers["location"].split("state=")[1]

    with patch("dashboard.routes.auth_routes.auth.exchange_code_for_token", return_value="token"), \
         patch(
             "dashboard.routes.auth_routes.auth.fetch_userinfo",
             return_value={"email": "stranger@example.com", "team_id": "T12345"},
         ):
        response = client.get("/auth/slack/callback", params={"code": "abc", "state": state})

    assert response.status_code == 403


def test_callback_sets_session_for_authorized_user(client):
    login_response = client.get("/login/slack")
    state = login_response.headers["location"].split("state=")[1]

    with patch("dashboard.routes.auth_routes.auth.exchange_code_for_token", return_value="token"), \
         patch(
             "dashboard.routes.auth_routes.auth.fetch_userinfo",
             return_value={"email": "a@example.com", "team_id": "T12345"},
         ):
        response = client.get("/auth/slack/callback", params={"code": "abc", "state": state})

    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/"


def test_logout_clears_session(client):
    response = client.post("/logout")
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/login"
```

- [ ] **Step 8: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_auth_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.routes.auth_routes'`

- [ ] **Step 9: `dashboard/routes/auth_routes.py` 구현**

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dashboard import auth
from dashboard.templating import templates

auth_router = APIRouter()


@auth_router.get("/login")
def login_page(request: Request, error: str = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@auth_router.get("/login/slack")
def login_slack(request: Request):
    config = request.app.state.config
    state = auth.generate_state()
    request.session["oauth_state"] = state
    redirect_uri = str(request.url_for("auth_callback"))
    url = auth.build_authorize_url(config, redirect_uri, state)
    return RedirectResponse(url)


@auth_router.get("/auth/slack/callback", name="auth_callback")
def auth_callback(request: Request, code: str = None, state: str = None):
    config = request.app.state.config
    expected_state = request.session.pop("oauth_state", None)
    if not code or not state or state != expected_state:
        return RedirectResponse("/login?error=state", status_code=303)

    redirect_uri = str(request.url_for("auth_callback"))
    try:
        access_token = auth.exchange_code_for_token(config, code, redirect_uri)
        userinfo = auth.fetch_userinfo(access_token)
    except Exception:
        return RedirectResponse("/login?error=slack", status_code=303)

    if not auth.is_authorized(userinfo, config):
        return HTMLResponse("<h1>접근 권한이 없습니다</h1>", status_code=403)

    request.session["user"] = {"email": userinfo["email"]}
    return RedirectResponse("/", status_code=303)


@auth_router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
```

- [ ] **Step 10: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_auth_routes.py -v`
Expected: PASS (6 passed)

- [ ] **Step 11: Commit**

```bash
git add dashboard/auth.py dashboard/templating.py dashboard/routes/auth_routes.py dashboard/templates/login.html tests/dashboard/test_auth.py tests/dashboard/test_auth_routes.py
git commit -m "feat: Slack OpenID Connect 로그인과 세션 인증 추가"
```

---

### Task 5: 공통 레이아웃 + 홈 대시보드

**Files:**
- Create: `dashboard/templates/base.html`
- Create: `dashboard/templates/home.html`
- Create: `dashboard/routes/home_routes.py`
- Test: `tests/dashboard/test_home_routes.py`

**Interfaces:**
- Consumes: `dashboard.db` (Task 2), `dashboard.auth.require_session`/`NotAuthenticated` (Task 4), `dashboard.templating.templates` (Task 4)
- Produces: `home_router: fastapi.APIRouter` — `GET /`

- [ ] **Step 1: `dashboard/templates/base.html` 작성**

```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <title>{% block title %}에러 로그 대시보드{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body class="bg-gray-50 min-h-screen flex">
  <aside class="w-56 bg-white border-r border-gray-100 flex flex-col">
    <div class="px-6 py-5 font-semibold text-gray-800">에러 로그</div>
    <nav class="flex-1 px-3 space-y-1 text-sm">
      <a href="/" class="block rounded-lg px-3 py-2 text-gray-700 hover:bg-gray-100">대시보드</a>
      <a href="/servers" class="block rounded-lg px-3 py-2 text-gray-700 hover:bg-gray-100">서버관리</a>
      <a href="/errors" class="block rounded-lg px-3 py-2 text-gray-700 hover:bg-gray-100">에러이력</a>
    </nav>
  </aside>
  <div class="flex-1 flex flex-col">
    <header class="h-16 bg-white border-b border-gray-100 flex items-center justify-end px-8 gap-4">
      <span class="text-sm text-gray-600">{{ user.email if user else "" }}</span>
      <form method="post" action="/logout">
        <button class="text-sm text-gray-500 hover:text-gray-800">로그아웃</button>
      </form>
    </header>
    <main class="flex-1 p-8">
      {% block content %}{% endblock %}
    </main>
  </div>
</body>
</html>
```

- [ ] **Step 2: 실패하는 테스트 작성 — `tests/dashboard/test_home_routes.py`**

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated, require_session
from dashboard.config import DashboardConfig
from dashboard.routes.home_routes import home_router

CONFIG = DashboardConfig(
    db_path="", slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="k",
)


@pytest.fixture
def app(tmp_path):
    config = DashboardConfig(**{**CONFIG.__dict__, "db_path": str(tmp_path / "test.db")})
    db.init_db(config.db_path)

    application = FastAPI()
    application.state.config = config
    application.add_middleware(SessionMiddleware, secret_key=config.session_secret)
    application.include_router(home_router)

    @application.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=303)

    return application


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


def test_home_redirects_when_not_logged_in(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_home_shows_stats_when_logged_in(app, client):
    config = app.state.config
    db.insert_server(config.db_path, "server-a", "h", 22, "u", "k", "l", "default", None)
    db.insert_error(
        config.db_path, "server-a", "2026-08-06T10:00:00+09:00", "ERROR",
        "java.lang.NullPointerException", "boom", "at ...", "raw",
        notified=True, notified_at="2026-08-06T10:00:05+09:00",
    )
    app.dependency_overrides[require_session] = lambda: {"email": "a@example.com"}

    response = client.get("/")

    assert response.status_code == 200
    assert "1" in response.text  # 서버 1대
```

`require_session`을 실제 세션 쿠키(서명 등)로 재현하는 대신 FastAPI의 `dependency_overrides`로 대체하는 이유: Starlette `SessionMiddleware`는 내부적으로 `itsdangerous.TimestampSigner`로 쿠키를 서명하는데, 이 서명 형식을 테스트 코드에서 직접 재현하려 하면 라이브러리 내부 구현에 결합되어 버전이 바뀌면 깨지기 쉽다. `dependency_overrides`는 FastAPI가 공식적으로 제공하는 테스트용 의존성 대체 메커니즘이라 더 안정적이다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_home_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.routes.home_routes'`

- [ ] **Step 4: `dashboard/templates/home.html` 작성**

```html
{% extends "base.html" %}
{% block content %}
<div class="grid grid-cols-3 gap-4 mb-6">
  <div class="bg-white rounded-2xl shadow-sm p-5">
    <p class="text-sm text-gray-500">등록된 서버</p>
    <p class="text-2xl font-semibold text-gray-800 mt-1">{{ server_count }}</p>
  </div>
  <div class="bg-white rounded-2xl shadow-sm p-5">
    <p class="text-sm text-gray-500">오늘 발생 에러</p>
    <p class="text-2xl font-semibold text-gray-800 mt-1">{{ errors_today }}</p>
  </div>
  <div class="bg-white rounded-2xl shadow-sm p-5">
    <p class="text-sm text-gray-500">오늘 알림 발송</p>
    <p class="text-2xl font-semibold text-gray-800 mt-1">{{ notified_today }}</p>
  </div>
</div>

<div class="bg-white rounded-2xl shadow-sm p-5 mb-6">
  <p class="text-sm text-gray-500 mb-3">최근 7일 에러 추이</p>
  <canvas id="trendChart" height="80"></canvas>
</div>

<div class="bg-white rounded-2xl shadow-sm p-5">
  <p class="text-sm text-gray-500 mb-3">최근 에러</p>
  <table class="w-full text-sm text-left">
    <thead class="text-gray-400">
      <tr><th class="py-1">시각</th><th>서버</th><th>타입</th><th>메시지</th></tr>
    </thead>
    <tbody>
      {% for e in recent %}
      <tr class="border-t border-gray-100">
        <td class="py-2">{{ e.timestamp }}</td>
        <td>{{ e.server_id }}</td>
        <td>{{ e.error_type }}</td>
        <td>{{ e.message }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<script>
new Chart(document.getElementById('trendChart'), {
  type: 'line',
  data: {
    labels: {{ trend_labels | tojson }},
    datasets: [{
      label: '에러 수',
      data: {{ trend_counts | tojson }},
      borderColor: '#2563eb',
      backgroundColor: 'rgba(37, 99, 235, 0.1)',
      fill: true,
      tension: 0.3,
    }],
  },
  options: { plugins: { legend: { display: false } } },
});
</script>
{% endblock %}
```

- [ ] **Step 5: `dashboard/routes/home_routes.py` 구현**

```python
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from dashboard import db
from dashboard.auth import require_session
from dashboard.templating import templates

home_router = APIRouter()


@home_router.get("/")
def home(request: Request, user: dict = Depends(require_session)):
    config = request.app.state.config
    now = datetime.now(timezone.utc).astimezone()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    week_start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

    daily_counts = db.error_counts_by_day(config.db_path, week_start)
    counts_by_day = {row["day"]: row["count"] for row in daily_counts}
    trend_labels = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    trend_counts = [counts_by_day.get(day, 0) for day in trend_labels]

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "user": user,
            "server_count": db.count_servers(config.db_path),
            "errors_today": db.count_errors_since(config.db_path, today_start),
            "notified_today": db.count_notified_since(config.db_path, today_start),
            "recent": db.recent_errors(config.db_path, limit=5),
            "trend_labels": trend_labels,
            "trend_counts": trend_counts,
        },
    )
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_home_routes.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/base.html dashboard/templates/home.html dashboard/routes/home_routes.py tests/dashboard/test_home_routes.py
git commit -m "feat: 공통 레이아웃과 홈 대시보드(통계 카드+추이 차트) 추가"
```

---

### Task 6: 서버 관리 화면

**Files:**
- Create: `dashboard/templates/servers.html`
- Create: `dashboard/templates/server_edit.html`
- Create: `dashboard/routes/servers_routes.py`
- Test: `tests/dashboard/test_servers_routes.py`

**Interfaces:**
- Consumes: `dashboard.db` (Task 2), `dashboard.validation.validate_server_fields`/`ServerValidationError` (Task 3), `dashboard.auth.require_session` (Task 4)
- Produces: `servers_router: fastapi.APIRouter` — `GET /servers`, `POST /servers`, `GET /servers/{server_id}/edit`, `POST /servers/{server_id}/edit`, `POST /servers/{server_id}/delete`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/dashboard/test_servers_routes.py`:
```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated, require_session
from dashboard.config import DashboardConfig
from dashboard.routes.servers_routes import servers_router

CONFIG_KWARGS = dict(
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="k",
)


@pytest.fixture
def app(tmp_path):
    config = DashboardConfig(db_path=str(tmp_path / "test.db"), **CONFIG_KWARGS)
    db.init_db(config.db_path)

    application = FastAPI()
    application.state.config = config
    application.add_middleware(SessionMiddleware, secret_key=config.session_secret)
    application.include_router(servers_router)

    @application.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=303)

    return application


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(app, client):
    app.dependency_overrides[require_session] = lambda: {"email": "a@example.com"}
    return client


def test_servers_page_requires_login(client):
    response = client.get("/servers")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_add_server_creates_record(app, logged_in_client):
    response = logged_in_client.post(
        "/servers",
        data={
            "server_id": "server-a", "host": "10.0.1.10", "port": "22",
            "username": "deploy", "ssh_key_path": "/k.pem",
            "log_path": "/var/log/app.log", "format": "default", "custom_pattern": "",
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/servers"
    server = db.get_server(app.state.config.db_path, "server-a")
    assert server["host"] == "10.0.1.10"


def test_add_server_with_invalid_custom_pattern_shows_error(app, logged_in_client):
    response = logged_in_client.post(
        "/servers",
        data={
            "server_id": "server-a", "host": "h", "port": "22",
            "username": "u", "ssh_key_path": "k", "log_path": "l",
            "format": "custom", "custom_pattern": "(?P<timestamp>[",
        },
    )

    assert response.status_code == 200
    assert "invalid custom_pattern regex" in response.text
    assert db.get_server(app.state.config.db_path, "server-a") is None


def test_edit_server_updates_record(app, logged_in_client):
    config = app.state.config
    db.insert_server(config.db_path, "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/a.log", "default", None)

    response = logged_in_client.post(
        "/servers/server-a/edit",
        data={
            "host": "10.0.1.99", "port": "2222", "username": "ops",
            "ssh_key_path": "/new.pem", "log_path": "/var/log/new.log",
            "format": "default", "custom_pattern": "",
        },
    )

    assert response.status_code == 303
    updated = db.get_server(config.db_path, "server-a")
    assert updated["host"] == "10.0.1.99"
    assert updated["port"] == 2222


def test_delete_server_removes_record(app, logged_in_client):
    config = app.state.config
    db.insert_server(config.db_path, "server-a", "h", 22, "u", "k", "l", "default", None)

    response = logged_in_client.post("/servers/server-a/delete")

    assert response.status_code == 303
    assert db.get_server(config.db_path, "server-a") is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_servers_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.routes.servers_routes'`

- [ ] **Step 3: `dashboard/templates/servers.html` 작성**

```html
{% extends "base.html" %}
{% block content %}
<div class="bg-white rounded-2xl shadow-sm p-5 mb-6">
  <p class="text-sm font-medium text-gray-700 mb-3">서버 추가</p>
  <form method="post" action="/servers" class="grid grid-cols-4 gap-3 text-sm">
    <input name="server_id" placeholder="server_id" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="host" placeholder="host" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="port" placeholder="port" value="22" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="username" placeholder="username" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="ssh_key_path" placeholder="ssh_key_path" class="border rounded-lg px-3 py-2 col-span-2" required>
    <input name="log_path" placeholder="log_path" class="border rounded-lg px-3 py-2 col-span-2" required>
    <select name="format" class="border rounded-lg px-3 py-2 col-span-1">
      <option value="default">default</option>
      <option value="custom">custom</option>
    </select>
    <input name="custom_pattern" placeholder="custom_pattern (선택)" class="border rounded-lg px-3 py-2 col-span-3">
    <button class="col-span-4 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2">추가</button>
  </form>
  {% if error %}<p class="text-sm text-red-600 mt-3">{{ error }}</p>{% endif %}
</div>

<div class="bg-white rounded-2xl shadow-sm p-5">
  <table class="w-full text-sm text-left">
    <thead class="text-gray-400">
      <tr><th class="py-1">server_id</th><th>host</th><th>format</th><th>등록일</th><th></th></tr>
    </thead>
    <tbody>
      {% for s in servers %}
      <tr class="border-t border-gray-100">
        <td class="py-2">{{ s.server_id }}</td>
        <td>{{ s.host }}</td>
        <td>{{ s.format }}</td>
        <td>{{ s.created_at }}</td>
        <td class="text-right space-x-2">
          <a href="/servers/{{ s.server_id }}/edit" class="text-blue-600">수정</a>
          <form method="post" action="/servers/{{ s.server_id }}/delete" class="inline">
            <button class="text-red-600">삭제</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: `dashboard/templates/server_edit.html` 작성**

```html
{% extends "base.html" %}
{% block content %}
<div class="bg-white rounded-2xl shadow-sm p-5 max-w-xl">
  <p class="text-sm font-medium text-gray-700 mb-3">{{ server.server_id }} 수정</p>
  <form method="post" action="/servers/{{ server.server_id }}/edit" class="grid grid-cols-2 gap-3 text-sm">
    <input name="host" value="{{ server.host }}" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="port" value="{{ server.port }}" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="username" value="{{ server.username }}" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="ssh_key_path" value="{{ server.ssh_key_path }}" class="border rounded-lg px-3 py-2 col-span-1" required>
    <input name="log_path" value="{{ server.log_path }}" class="border rounded-lg px-3 py-2 col-span-2" required>
    <select name="format" class="border rounded-lg px-3 py-2 col-span-1">
      <option value="default" {% if server.format == "default" %}selected{% endif %}>default</option>
      <option value="custom" {% if server.format == "custom" %}selected{% endif %}>custom</option>
    </select>
    <input name="custom_pattern" value="{{ server.custom_pattern or '' }}" class="border rounded-lg px-3 py-2 col-span-1">
    <button class="col-span-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2">저장</button>
  </form>
  {% if error %}<p class="text-sm text-red-600 mt-3">{{ error }}</p>{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: `dashboard/routes/servers_routes.py` 구현**

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from dashboard import db
from dashboard.auth import require_session
from dashboard.templating import templates
from dashboard.validation import ServerValidationError, validate_server_fields

servers_router = APIRouter()


@servers_router.get("/servers")
def list_servers_page(request: Request, user: dict = Depends(require_session)):
    config = request.app.state.config
    return templates.TemplateResponse(
        "servers.html",
        {"request": request, "user": user, "servers": db.list_servers(config.db_path), "error": None},
    )


@servers_router.post("/servers")
def add_server(
    request: Request,
    server_id: str = Form(...), host: str = Form(...), port: int = Form(...),
    username: str = Form(...), ssh_key_path: str = Form(...), log_path: str = Form(...),
    format: str = Form(...), custom_pattern: str = Form(""),
    user: dict = Depends(require_session),
):
    config = request.app.state.config
    pattern = custom_pattern or None
    try:
        validate_server_fields(server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    except ServerValidationError as e:
        return templates.TemplateResponse(
            "servers.html",
            {"request": request, "user": user, "servers": db.list_servers(config.db_path), "error": str(e)},
        )

    db.insert_server(config.db_path, server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    return RedirectResponse("/servers", status_code=303)


@servers_router.get("/servers/{server_id}/edit")
def edit_server_page(request: Request, server_id: str, user: dict = Depends(require_session)):
    config = request.app.state.config
    server = db.get_server(config.db_path, server_id)
    return templates.TemplateResponse(
        "server_edit.html", {"request": request, "user": user, "server": server, "error": None}
    )


@servers_router.post("/servers/{server_id}/edit")
def edit_server(
    request: Request, server_id: str,
    host: str = Form(...), port: int = Form(...), username: str = Form(...),
    ssh_key_path: str = Form(...), log_path: str = Form(...),
    format: str = Form(...), custom_pattern: str = Form(""),
    user: dict = Depends(require_session),
):
    config = request.app.state.config
    pattern = custom_pattern or None
    try:
        validate_server_fields(server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    except ServerValidationError as e:
        server = {
            "server_id": server_id, "host": host, "port": port, "username": username,
            "ssh_key_path": ssh_key_path, "log_path": log_path, "format": format, "custom_pattern": pattern,
        }
        return templates.TemplateResponse(
            "server_edit.html", {"request": request, "user": user, "server": server, "error": str(e)}
        )

    db.update_server(config.db_path, server_id, host, port, username, ssh_key_path, log_path, format, pattern)
    return RedirectResponse("/servers", status_code=303)


@servers_router.post("/servers/{server_id}/delete")
def delete_server(request: Request, server_id: str, user: dict = Depends(require_session)):
    config = request.app.state.config
    db.delete_server(config.db_path, server_id)
    return RedirectResponse("/servers", status_code=303)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_servers_routes.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/servers.html dashboard/templates/server_edit.html dashboard/routes/servers_routes.py tests/dashboard/test_servers_routes.py
git commit -m "feat: 서버 등록/수정/삭제 화면 추가"
```

---

### Task 7: 에러 이력 화면

**Files:**
- Create: `dashboard/templates/errors.html`
- Create: `dashboard/routes/errors_routes.py`
- Test: `tests/dashboard/test_errors_routes.py`

**Interfaces:**
- Consumes: `dashboard.db.query_errors`/`list_servers` (Task 2), `dashboard.auth.require_session` (Task 4)
- Produces: `errors_router: fastapi.APIRouter` — `GET /errors`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/dashboard/test_errors_routes.py`:
```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated, require_session
from dashboard.config import DashboardConfig
from dashboard.routes.errors_routes import errors_router

CONFIG_KWARGS = dict(
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="k",
)


@pytest.fixture
def app(tmp_path):
    config = DashboardConfig(db_path=str(tmp_path / "test.db"), **CONFIG_KWARGS)
    db.init_db(config.db_path)

    application = FastAPI()
    application.state.config = config
    application.add_middleware(SessionMiddleware, secret_key=config.session_secret)
    application.include_router(errors_router)

    @application.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=303)

    return application


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(app, client):
    app.dependency_overrides[require_session] = lambda: {"email": "a@example.com"}
    return client


def test_errors_page_requires_login(client):
    response = client.get("/errors")
    assert response.status_code == 303


def test_errors_page_lists_all_by_default(app, logged_in_client):
    config = app.state.config
    db.insert_error(config.db_path, "server-a", "2026-08-06T10:00:00+09:00", "ERROR",
                     "java.lang.NullPointerException", "boom", "at ...", "raw")

    response = logged_in_client.get("/errors")

    assert response.status_code == 200
    assert "java.lang.NullPointerException" in response.text


def test_errors_page_filters_by_server_id(app, logged_in_client):
    config = app.state.config
    db.insert_error(config.db_path, "server-a", "2026-08-06T10:00:00+09:00", "ERROR", "Type-A", "m", "s", "r")
    db.insert_error(config.db_path, "server-b", "2026-08-06T10:00:00+09:00", "ERROR", "Type-B", "m", "s", "r")

    response = logged_in_client.get("/errors", params={"server_id": "server-b"})

    assert "Type-B" in response.text
    assert "Type-A" not in response.text
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_errors_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.routes.errors_routes'`

- [ ] **Step 3: `dashboard/templates/errors.html` 작성**

```html
{% extends "base.html" %}
{% block content %}
<div class="bg-white rounded-2xl shadow-sm p-5 mb-6">
  <form method="get" action="/errors" class="grid grid-cols-4 gap-3 text-sm">
    <select name="server_id" class="border rounded-lg px-3 py-2">
      <option value="">전체 서버</option>
      {% for s in servers %}
      <option value="{{ s.server_id }}" {% if s.server_id == filters.server_id %}selected{% endif %}>{{ s.server_id }}</option>
      {% endfor %}
    </select>
    <input type="date" name="date_from" value="{{ filters.date_from or '' }}" class="border rounded-lg px-3 py-2">
    <input type="date" name="date_to" value="{{ filters.date_to or '' }}" class="border rounded-lg px-3 py-2">
    <input type="text" name="error_type" placeholder="에러 타입 검색" value="{{ filters.error_type or '' }}" class="border rounded-lg px-3 py-2">
    <button class="col-span-4 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2">검색</button>
  </form>
</div>

<div class="bg-white rounded-2xl shadow-sm p-5">
  <table class="w-full text-sm text-left">
    <thead class="text-gray-400">
      <tr><th class="py-1">시각</th><th>서버</th><th>타입</th><th>메시지</th><th>알림</th></tr>
    </thead>
    <tbody>
      {% for e in errors %}
      <tr class="border-t border-gray-100">
        <td class="py-2">{{ e.timestamp }}</td>
        <td>{{ e.server_id }}</td>
        <td>{{ e.error_type }}</td>
        <td>{{ e.message }}</td>
        <td>{{ "발송됨" if e.notified else "-" }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: `dashboard/routes/errors_routes.py` 구현**

```python
from fastapi import APIRouter, Depends, Request

from dashboard import db
from dashboard.auth import require_session
from dashboard.templating import templates

errors_router = APIRouter()


@errors_router.get("/errors")
def list_errors_page(
    request: Request,
    server_id: str = None, date_from: str = None, date_to: str = None, error_type: str = None,
    user: dict = Depends(require_session),
):
    config = request.app.state.config
    errors = db.query_errors(
        config.db_path,
        server_id=server_id or None,
        date_from=date_from or None,
        date_to=date_to or None,
        error_type=error_type or None,
    )
    return templates.TemplateResponse(
        "errors.html",
        {
            "request": request,
            "user": user,
            "errors": errors,
            "servers": db.list_servers(config.db_path),
            "filters": {
                "server_id": server_id, "date_from": date_from,
                "date_to": date_to, "error_type": error_type,
            },
        },
    )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_errors_routes.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/errors.html dashboard/routes/errors_routes.py tests/dashboard/test_errors_routes.py
git commit -m "feat: 에러 이력 조회/필터 화면 추가"
```

---

### Task 8: 앱 조립 (`main.py`) + 메모리 문서화

**Files:**
- Create: `dashboard/main.py`
- Test: `tests/dashboard/test_main.py`
- Create: `.claude/memory/dashboard/error-ingestion-api-contract.md`

**Interfaces:**
- Consumes: 모든 이전 태스크의 `*_router`, `load_config_from_env`, `db.init_db`
- Produces: `create_app(config) -> fastapi.FastAPI` (순수 팩토리, import 시점에 부작용 없음), `app_factory() -> fastapi.FastAPI` (환경변수를 읽어 `create_app`을 호출 — 실제 실행 시에만, `uvicorn dashboard.main:app_factory --factory`로 구동)

**주의:** `create_app`은 `dashboard.main` 모듈을 **import만 해도 즉시 실행되지 않아야 한다**. `app = create_app(load_config_from_env())`처럼 모듈 최상단에서 즉시 호출하면, 테스트가 `from dashboard.main import create_app`만 해도 실제 환경변수를 요구하고 기본 DB 경로(`dashboard/data.db`)에 실제 파일을 만드는 부작용이 생긴다. 환경변수를 읽는 시점은 `app_factory()`가 실제로 호출될 때(서버 구동 시점)로 미룬다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/dashboard/test_main.py`:
```python
import pytest

from dashboard.config import DashboardConfig
from dashboard.main import create_app


@pytest.fixture
def config(tmp_path):
    return DashboardConfig(db_path=str(tmp_path / "test.db"), slack_client_id="c",
                            slack_client_secret="s", slack_team_id="T1",
                            allowed_emails=["a@example.com"], session_secret="secret", api_key="key")


def test_create_app_initializes_db_and_mounts_routes(config):
    app = create_app(config)

    from fastapi.testclient import TestClient
    client = TestClient(app, follow_redirects=False)

    assert client.get("/login").status_code == 200
    assert client.get("/api/servers", headers={"X-API-Key": "key"}).status_code == 200
    assert client.get("/").status_code == 303  # not logged in -> redirect
```

이 테스트는 `create_app`만 호출한다 — `app_factory`나 `load_config_from_env`는 호출하지 않으므로 실제 환경변수나 기본 DB 경로에 의존하지 않는다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/dashboard/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.main'`

- [ ] **Step 3: `dashboard/main.py` 구현**

```python
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated
from dashboard.config import load_config_from_env
from dashboard.routes.api import api_router
from dashboard.routes.auth_routes import auth_router
from dashboard.routes.errors_routes import errors_router
from dashboard.routes.home_routes import home_router
from dashboard.routes.servers_routes import servers_router


def create_app(config):
    db.init_db(config.db_path)

    app = FastAPI()
    app.state.config = config
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret)

    app.include_router(auth_router)
    app.include_router(home_router)
    app.include_router(servers_router)
    app.include_router(errors_router)
    app.include_router(api_router)

    @app.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        return RedirectResponse("/login", status_code=303)

    return app


def app_factory():
    """실제 서버 구동 시 환경변수를 읽어 앱을 만든다.
    실행: uvicorn dashboard.main:app_factory --factory
    """
    return create_app(load_config_from_env())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/dashboard/test_main.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: 전체 스위트 실행**

Run: `pytest -v`
Expected: `watcher/` 기존 40개 + `dashboard/` 신규 테스트(config 3 + db 16 + validation 5 + api 5 + auth 7 + auth_routes 6 + home_routes 2 + servers_routes 5 + errors_routes 3 + main 1 = 53개), 총 93개 전부 PASS.

- [ ] **Step 6: `POST /api/errors` 계약 문서화**

`.claude/memory/dashboard/error-ingestion-api-contract.md`:
```markdown
# 에러 저장 API 계약 (analyzer가 호출, dashboard가 구현)

- 작성일: 2026-08-06
- 관련 설계 문서: docs/superpowers/specs/2026-08-06-dashboard-design.md
- 관련 구현: dashboard/routes/api.py의 POST /api/errors

## 결론 요약
analyzer는 OpenAI 분석 + Redis 중복 제거 + Slack 알림 처리가 끝난 에러 1건마다
아래 API를 호출해 SQLite에 저장을 위임한다.

## API

```
POST /api/errors
Header: X-API-Key: <DASHBOARD_API_KEY>
Content-Type: application/json

{
  "server_id": "server-a",
  "timestamp": "2026-08-06T12:35:01+09:00",
  "log_level": "ERROR",
  "error_type": "java.lang.NullPointerException",
  "message": "...",
  "stack_trace": "...",
  "raw_log": "...",
  "ai_analysis": "원인: ... / 해결방향: ...",
  "notified": true,
  "notified_at": "2026-08-06T12:35:05+09:00"
}
```

`server_id`~`raw_log` 7개 필드는 필수이며 watcher의 ErrorEvent 스키마와 동일하다.
`ai_analysis`/`notified`(기본 false)/`notified_at`은 analyzer가 채우는 선택 필드다.

## 응답
- 200: `{"status": "ok"}`
- 401: `X-API-Key` 없음/불일치
- 422: 필수 필드 누락 (FastAPI 기본 검증 오류 형식)
```

- [ ] **Step 7: Commit**

```bash
git add dashboard/main.py tests/dashboard/test_main.py .claude/memory/dashboard/error-ingestion-api-contract.md
git commit -m "feat: FastAPI 앱 조립 및 analyzer용 에러 저장 API 계약 문서화"
```
