# log-watcher 설계 문서

- 작성일: 2026-08-05
- 담당 에이전트: log-watcher
- 담당 경로: `watcher/`
- 프로젝트: ai-log-analyzer (python_log 토이프로젝트)

## 1. 배경 및 전체 아키텍처

Spring Boot 서버 로그에서 에러가 발생하면 OpenAI로 원인을 분석하고, Redis로 중복 알림을 제거한 뒤 Slack으로 알림을 보내고, SQLite에 이력을 저장해 FastAPI 대시보드로 조회하는 파이프라인이다. 감시 대상 Spring Boot 서버는 서로 다른 원격 머신에 여러 대가 존재하며, 로그 포맷도 서버마다 기본(logback) 또는 커스텀 포맷으로 다르다.

전체 파이프라인은 다음과 같이 3개의 하위 프로젝트로 나누어 순서대로 설계·구현한다.

```
[원격 서버 A/B/C ...]
    │ (watcher/) 경량 에이전트: 로그 감시 → 에러 추출 → 중앙 서버로 HTTP 전송
    ▼
[analyzer/] 중앙 서버: 수집 API → OpenAI 분석 → Redis 중복 제거 → Slack 알림
    ▼
[dashboard/] SQLite 저장 + FastAPI REST API + 간단한 HTML 목록 화면
```

이 문서는 파이프라인의 첫 단계인 **log-watcher (원격 에이전트)** 를 다룬다. analyzer/dashboard는 이 문서에서 정의하는 에러 이벤트 스키마를 입력으로 받아 별도 설계 문서에서 다룬다.

## 2. log-watcher 범위

각 원격 Spring Boot 서버에 상주하는 경량 Python 에이전트로, 다음을 수행한다.

1. 서버별 설정 파일에 지정된 로그 파일 1개를 watchdog으로 실시간 감시
2. 새로 추가된 로그 라인 중 에러/예외를 감지하여 구조화된 이벤트로 파싱
3. 파싱된 이벤트를 중앙 analyzer 서버로 HTTP POST 전송 (실패 시 로컬 버퍼링 후 재시도)

범위 밖: 원격 서버에 에이전트를 배포/설치하는 자동화(Ansible 등), 여러 로그 파일 동시 감시, 원격 SSH 수집 — 모두 이번 하위 프로젝트에서는 다루지 않는다 (필요 시 이후 확장 과제).

## 3. 구성 요소

```
watcher/
├── config/
│   └── <server_id>.yaml     # 서버별 설정 파일
├── main.py                  # watchdog 감시 진입점
├── parser.py                # 로그 라인 → 에러 이벤트 파싱
├── sender.py                # HTTP 전송 + 재시도/버퍼링
└── models.py                # 에러 이벤트 데이터 구조
```

### 3.1 설정 파일 (`config/<server_id>.yaml`)

```yaml
server_id: server-a
log_path: /var/log/app/application.log
format: default        # default | custom
custom_pattern: null    # format이 custom일 때 named group 정규식
central_endpoint: https://collector.example.com/api/errors
api_key_env: WATCHER_API_KEY   # 실제 키 값은 이 이름의 환경변수에서 로드
```

- `format: default` — 표준 Spring Boot logback 패턴(`yyyy-MM-dd HH:mm:ss.SSS [thread] LEVEL logger - message`, 이후 스택트레이스)을 내장 정규식으로 처리한다.
- `format: custom` — `custom_pattern`에 지정된 정규식(named group: `timestamp`, `level`, `logger`, `message` 최소 포함)으로 처리한다.
- API 키 등 민감 정보는 설정 파일에 직접 쓰지 않고 `api_key_env`로 지정한 환경변수명을 통해서만 로드한다 (프로젝트 규칙: API 키 하드코딩 금지).

### 3.2 에러 이벤트 스키마

log-watcher가 파싱해 analyzer로 전송하는 JSON 스키마. analyzer/dashboard 설계 문서에서도 그대로 참조하므로 변경 시 `.claude/memory/log-watcher/`에 갱신 이력을 남긴다.

```json
{
  "server_id": "server-a",
  "timestamp": "2026-08-05T12:34:56+09:00",
  "log_level": "ERROR",
  "error_type": "java.lang.NullPointerException",
  "message": "Cannot invoke ...",
  "stack_trace": "at com.example...\n...",
  "raw_log": "원본 로그 라인 전체 (스택트레이스 포함)"
}
```

### 3.3 에러 판별 로직

- 로그 레벨이 `ERROR`인 라인, 또는 `Exception`/`Caused by:`를 포함하는 라인을 에러 시작으로 판단한다.
- 에러 시작 라인부터, 다음 로그 엔트리(새 타임스탬프로 시작하는 라인)가 나타나기 전까지의 모든 라인을 해당 에러의 스택트레이스로 묶는다.
- 하나로 묶인 원문 전체를 `raw_log`에 저장하고, 첫 줄에서 `timestamp`/`log_level`/`message`를, 스택트레이스에서 최상위 예외 클래스명을 `error_type`으로 추출한다.

### 3.4 전송 및 장애 처리 (`sender.py`)

- 파싱된 이벤트를 `central_endpoint`로 HTTP POST, 헤더 `X-API-Key: <api_key_env 환경변수 값>` 포함.
- 전송 실패(네트워크 오류, timeout, 5xx 응답) 시 이벤트를 로컬 큐 파일(`watcher/.queue/<server_id>.jsonl` 등)에 append하고, 백오프 간격(예: 30초 → 최대 5분)으로 재전송을 시도한다.
- 전송 성공 시 큐에서 해당 이벤트를 제거한다. 큐 파일은 프로세스 재시작 후에도 남아있는 이벤트를 이어서 재전송할 수 있어야 한다.

## 4. 에러 처리

- 설정 파일 파싱 실패(필수 필드 누락, 잘못된 정규식 등)는 시작 시점에 즉시 실패시키고 원인을 로그로 남긴다 (조용히 무시하지 않는다).
- `custom_pattern`이 특정 라인에 매치되지 않는 경우, 해당 라인은 무시하고 다음 라인에서 재시도한다 (프로세스를 중단시키지 않는다).
- 감시 중인 로그 파일이 로테이션(파일 교체)되는 경우 watchdog이 새 파일 생성을 감지해 감시를 이어간다.

## 5. 테스트 전략

- **파서 단위 테스트**: `format: default`/`format: custom` 각각에 대해 샘플 로그 fixture(정상 에러, 여러 줄 스택트레이스, 매치 안 되는 라인 포함)로 `parser.py`를 검증한다.
- **watchdog 통합 테스트**: 임시 디렉터리에 로그 파일을 만들고 실시간으로 라인을 append하면서 `main.py`가 이벤트를 감지하는지 확인한다.
- **sender 재시도 테스트**: mock HTTP 서버(성공/실패/timeout 응답)로 `sender.py`의 재시도·백오프·큐 적재/소진 동작을 검증한다.

## 6. 다음 단계

이 설계가 승인되면 `.claude/memory/log-watcher/error-event-schema.md`에 위 3.2 스키마를 문서화하여 이후 analyzer 설계 시 참조할 수 있게 하고, writing-plans 스킬로 구현 계획을 작성한다.
