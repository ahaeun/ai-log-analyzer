# log-watcher SSH 중앙집중식 재설계

- 작성일: 2026-08-06
- 담당 에이전트: log-watcher
- 담당 경로: `watcher/`
- 프로젝트: ai-log-analyzer (python_log 토이프로젝트)
- 이전 설계 문서: docs/superpowers/specs/2026-08-05-log-watcher-design.md (이 문서로 대체됨)

## 1. 배경 및 변경 이유

기존 log-watcher 설계(2026-08-05)는 각 원격 Spring Boot 서버에 경량 Python 에이전트를 상주시키는 방식이었다. 이 방식은 **운영 서버마다 Python과 의존성을 설치해야 한다**는 제약이 있었다.

이를 없애기 위해 아키텍처를 다음과 같이 바꾼다: 원격 서버에는 아무것도 설치하지 않고, **중앙 watcher 프로세스 하나가 SSH로 각 서버의 로그 파일을 원격으로 tail**한다. 원격 서버에 필요한 것은 SSH 접속 가능 여부뿐이다.

또한 감시할 서버 목록을 서버별 정적 YAML 파일 대신, **dashboard에 등록된 서버 목록을 동적으로 조회**하는 방식으로 바꾼다. dashboard에서 서버를 추가/삭제하면 watcher가 이를 감지해 자동으로 감시를 시작/중단한다.

기존에 구현되어 있던 서버별 상주 에이전트 방식의 `watcher/` 코드(설정 파일 로더, watchdog 연동 등)는 이 재설계로 **완전히 대체**된다. 단, 에러 라인 그룹핑 로직(`parser.py`)은 "라인을 어디서 얻어오는가"와 무관하게 설계되어 있어 **변경 없이 그대로 재사용**한다.

## 2. 전체 아키텍처

```
[dashboard] (별도 하위 프로젝트, 이 문서에서는 계약만 정의)
    │  GET /api/servers → 등록된 서버 목록 반환
    ▼
[중앙 watcher 프로세스]
    registry_client.py — 30초마다 GET /api/servers 호출, 서버 목록 조회
    ssh_tail.py        — 서버별로 SSH 연결을 계속 유지하며 15초마다 새 로그 바이트만 가져옴
    parser.py          — (재사용, 무변경) 에러/스택트레이스 그룹핑 → ErrorEvent
    sender.py          — watcher 전체가 공유하는 설정으로 analyzer에 HTTP POST, 실패 시 서버별 큐 파일에 재시도
    main.py            — 레지스트리 폴링(30s)과 서버별 tail 루프(15s)를 조율,
                          서버 추가/삭제를 감지해 tail을 자동으로 시작/종료
    ▼
[analyzer] (별도 하위 프로젝트) — OpenAI 분석 → Redis 중복 제거 → Slack 알림
    ▼
[dashboard] — SQLite 저장 + FastAPI 대시보드
```

## 3. 범위

이번 재설계는 **watcher만** 다룬다. dashboard의 서버 등록 CRUD 화면/API 구현은 범위 밖이며, 아래 3.1의 API 계약만 정의해 dashboard 쪽 후속 작업의 기준으로 삼는다.

## 3.1 dashboard와의 계약 (watcher가 호출한다고 가정하는 API)

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

- 이전 서버별 YAML의 `central_endpoint`/`api_key_env`는 watcher 전체가 공유하는 설정으로 이동했으므로 서버 항목에는 없다.
- `format`/`custom_pattern`의 의미는 기존과 동일 (`default` = 표준 logback 패턴, `custom` = `custom_pattern` 정규식 사용, named group `timestamp`/`level`/`message` 최소 포함).
- SSH 인증은 개인키 파일 경로(`ssh_key_path`)만 사용한다. 키 파일 자체는 watcher가 실행되는 중앙 서버의 파일시스템에만 존재하며 네트워크로 전송되지 않는다.

## 4. 모듈 구성

```
watcher/
├── models.py           — ServerEntry, WatcherConfig
├── registry_client.py  — fetch_servers(registry_url) -> list[ServerEntry]
├── ssh_tail.py          — SSHTailer (서버 1개당 1개 인스턴스)
├── parser.py            — (재사용, 무변경) LogParser, ErrorEventAccumulator
├── sender.py            — EventSender (다중 서버 큐 파일 관리로 수정)
└── main.py              — WatcherManager (레지스트리/tail 폴링 조율)
```

### 4.1 `models.py`

```python
@dataclass
class ServerEntry:
    server_id: str
    host: str
    port: int
    username: str
    ssh_key_path: str
    log_path: str
    format: str              # "default" | "custom"
    custom_pattern: str | None = None
    # __post_init__ 검증은 기존 ServerConfig와 동일 (format 값, custom_pattern 필수 여부)

@dataclass
class WatcherConfig:
    registry_url: str
    analyzer_endpoint: str
    api_key_env: str
    queue_dir: str = "watcher/.queue"
    registry_poll_interval: int = 30
    log_poll_interval: int = 15
```

### 4.2 `registry_client.py`

`fetch_servers(registry_url) -> list[ServerEntry]`:
- `requests.get(registry_url, timeout=5)` 호출, JSON 파싱
- 각 항목을 `ServerEntry`로 변환하며 개별적으로 검증한다. **한 항목이 잘못돼도(정규식 오류 등) 나머지 유효한 항목은 반환한다** — 잘못된 항목은 건너뛰고 어떤 `server_id`가 왜 걸러졌는지 반환값에 포함시켜 `main.py`가 로그로 남길 수 있게 한다.
- 레지스트리 자체가 응답하지 않는 경우(`requests.RequestException`) 예외를 그대로 전파한다 — 재시도 여부 판단은 `main.py`의 폴링 루프가 담당한다 (마지막으로 알고 있던 목록 유지).

### 4.3 `ssh_tail.py`

`SSHTailer(entry: ServerEntry)`:
- `connect()`: `paramiko.SSHClient`로 `entry.host`/`port`/`username`/`ssh_key_path`를 사용해 접속. 최초 접속 성공 시 `stat -c%s <log_path>` 실행해 현재 파일 크기를 `offset`으로 저장(과거 이력은 건너뛰고 이후 추가분만 감시 — 기존 설계와 동일한 원칙).
- `read_new_bytes() -> str`: 연결이 없으면 `connect()` 시도(실패 시 빈 문자열 반환, 다음 호출에서 재시도). 연결돼 있으면 `stat -c%s <log_path>`로 현재 크기 확인 → `offset`보다 크면 `tail -c +<offset+1> <log_path>` 실행해 새 바이트를 가져오고 `offset`을 갱신. 파일 크기가 `offset`보다 작으면(로테이션) `offset`을 0으로 리셋하고 파일 전체를 다시 읽는다.
- 원격 명령 실행 중 예외 발생 시 연결을 끊긴 것으로 표시하고 빈 문자열을 반환한다 (다음 호출에서 재접속).

### 4.4 `sender.py` (기존 코드 수정)

- `EventSender.__init__(self, config: WatcherConfig)` — 더 이상 서버별 `ServerConfig`를 받지 않고, watcher 전체가 공유하는 `WatcherConfig`를 받는다.
- 큐 파일 경로는 이벤트마다 동적으로 결정: `os.path.join(config.queue_dir, f"{event.server_id}.jsonl")`.
- `flush_queue()`는 `queue_dir` 안의 모든 `*.jsonl` 파일을 순회하며 각각 재시도한다 (기존에는 파일 하나만 다뤘음).
- API 키 로딩(`os.environ.get(config.api_key_env)`), 실패 분류(`DELIVERED`/`RETRY`/`FAILED`), 락을 이용한 동시성 안전성, 생성자에서의 fail-fast 검증(env var 미설정 시 `ValueError`)은 기존 로직을 그대로 유지한다.

### 4.5 `main.py` — `WatcherManager`

- 서버별 활성 상태를 `dict[server_id -> (SSHTailer, ErrorEventAccumulator)]`로 관리한다.
- **레지스트리 루프** (`registry_poll_interval`마다): `fetch_servers()` 호출 → 현재 `dict`의 키와 비교해 새로 추가된 `server_id`는 `SSHTailer`+`ErrorEventAccumulator`를 새로 만들어 등록하고, 사라진 `server_id`는 `dict`에서 제거한다 (연결은 자연스럽게 GC됨).
- **tail 루프** (`log_poll_interval`마다, 활성 서버 전체 순회): 각 서버의 `SSHTailer.read_new_bytes()`로 새 텍스트를 가져와 줄 단위로 분리 → 해당 서버의 `ErrorEventAccumulator.feed_line()`에 순서대로 전달 → 완료된 이벤트는 공유 `EventSender.send()`로 전송.
- 두 루프는 별도 스레드에서 동작하며, 활성 서버 `dict`에 대한 접근은 락으로 보호한다.

## 5. 에러 처리

- 레지스트리(dashboard) 응답 없음: 마지막으로 알고 있던 서버 목록을 유지하고 경고 로그만 남긴 뒤 다음 주기에 재시도한다 (fail-soft — 런타임 중 일시적 장애이므로 설정 파싱 실패와 다르게 다룬다).
- 레지스트리 응답 중 일부 서버 항목이 잘못됨: 해당 항목만 건너뛰고 나머지는 정상 처리한다.
- SSH 연결 실패/끊김: 해당 서버의 tail만 실패 처리하고 다음 주기에 재접속을 시도한다. 다른 서버는 영향받지 않는다.
- 로그 파일 로테이션: `SSHTailer`가 파일 크기 감소를 감지해 `offset`을 리셋한다.

## 6. 테스트 전략

- `registry_client`: `requests.get`을 mock해 정상 목록, 일부 항목만 잘못된 목록(부분 실패), 레지스트리 자체 응답 실패를 검증.
- `ssh_tail`: `paramiko.SSHClient`를 mock해 최초 연결 시 offset 설정, 이후 증분 읽기, 연결 실패 후 재시도, 로테이션(크기 감소) 시 offset 리셋을 검증. 실제 SSH 연결은 사용하지 않는다.
- `sender`: 기존 단일 큐 파일 테스트를 확장해 서로 다른 `server_id`의 큐 파일이 독립적으로 쌓이고 재시도되는 것을 검증.
- `main.py` (`WatcherManager`): `registry_client`/`ssh_tail`을 mock해 서버 추가 시 tailer가 생성되고, 레지스트리에서 사라지면 tailer가 정리되는지 검증.
- `parser.py`는 변경이 없으므로 기존 테스트를 그대로 유지한다 (재검증 불필요).

## 7. 다음 단계

이 설계가 승인되면 기존 `watcher/` 코드(YAML 설정 로더, watchdog 연동)를 이 설계에 맞춰 교체하는 구현 계획을 작성한다. `parser.py`와 그 테스트는 변경 없이 유지한다. dashboard의 서버 등록 API(3.1의 계약)는 이후 dashboard 하위 프로젝트에서 별도로 구현한다.
