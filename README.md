# ai-log-analyzer

Spring Boot 서버 로그에서 에러가 발생하면 자동으로 감지해 OpenAI로 원인을 분석하고, Redis로 중복 알림을 제거한 뒤 Slack으로 알림을 보내고, SQLite에 이력을 저장해 FastAPI 대시보드로 조회하는 토이 프로젝트입니다.

## 파이프라인

```
[모니터링 대상 Spring Boot 서버 여러 대]
        │  (SSH로 원격 로그 파일을 tail — 서버에는 아무것도 설치하지 않음)
        ▼
┌───────────────┐   POST /api/errors    ┌───────────────┐   POST /api/errors    ┌───────────────┐
│   watcher     │ ────(X-API-Key)────▶  │   analyzer    │ ────(X-API-Key)────▶  │   dashboard   │
│ SSH 원격 감시 │                       │ OpenAI 분석    │                       │ SQLite 저장   │
│ + 에러 추출   │                       │ Redis 중복제거 │                       │ Slack 로그인  │
│               │ ◀── GET /api/servers ─│ Slack 알림    │                       │ FastAPI 화면  │
└───────────────┘   (서버 등록 목록)    └───────────────┘                       └───────────────┘
```

- **watcher**: 중앙 프로세스 하나가 SSH로 여러 원격 서버의 로그 파일을 직접 tail합니다. 감시할 서버 목록은 dashboard의 `GET /api/servers`를 30초마다 폴링해 동적으로 갱신합니다(대시보드에서 서버를 등록/삭제하면 자동으로 반영).
- **analyzer**: watcher가 보낸 에러를 받으면 즉시 `202`를 응답하고, 백그라운드에서 OpenAI로 원인을 분석하고, Redis로 10분 내 동일 에러(서버+타입+메시지) 중복 알림을 억제하고, Slack Webhook으로 알림을 보낸 뒤 dashboard에 저장을 위임합니다.
- **dashboard**: 서버 등록 관리(등록/수정/삭제), 에러 이력 조회(필터+페이지네이션), Slack 로그인(워크스페이스+이메일 허용목록 제한), 통계 카드/추이 차트가 있는 홈 화면을 제공합니다.

각 컴포넌트가 이미 만들어져 있는 상대방의 API 계약(스키마)에 맞춰 서로 통신하며, 그 계약은 `.claude/memory/`에 문서화되어 있습니다.

## 사전 준비물

- Python 3.9+
- Docker (로컬 Redis 실행용)
- OpenAI API 키
- Slack Incoming Webhook URL (알림 발송용) + Slack App Client ID/Secret (대시보드 로그인용)
- SSH로 접속 가능한 모니터링 대상 서버(들)와 그 서버들의 개인키

## 설치

```bash
python3 -m pip install -r requirements.txt
docker compose up -d   # Redis 실행 (analyzer의 중복 알림 억제용)
```

## 구성 요소별 실행 방법

### 1. dashboard (먼저 띄우는 걸 추천 — watcher/analyzer가 이 서비스를 호출합니다)

환경변수:

| 변수 | 설명 |
|---|---|
| `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` | Slack OAuth 앱 자격증명 (Sign in with Slack) |
| `SLACK_TEAM_ID` | 로그인을 허용할 Slack 워크스페이스 ID |
| `DASHBOARD_ALLOWED_EMAILS` | 로그인을 허용할 이메일 목록 (쉼표로 구분) |
| `DASHBOARD_SESSION_SECRET` | 로그인 세션 쿠키 서명 키 |
| `DASHBOARD_API_KEY` | watcher/analyzer가 이 값을 `X-API-Key`로 보내야 함 |
| `DASHBOARD_DB_PATH` (선택) | SQLite 파일 경로, 기본값 `dashboard/data.db` |

실행:

```bash
uvicorn dashboard.main:app_factory --factory --port 8000
```

### 2. analyzer

환경변수:

| 변수 | 설명 |
|---|---|
| `ANALYZER_API_KEY` | watcher가 이 값을 `X-API-Key`로 보내야 함 |
| `OPENAI_API_KEY` | OpenAI 에러 분석용 |
| `REDIS_URL` | 예: `redis://localhost:6379/0` |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `DASHBOARD_URL` | dashboard 서비스 베이스 URL, 예: `http://localhost:8000` |
| `DASHBOARD_API_KEY` | dashboard의 `DASHBOARD_API_KEY`와 동일한 값 |

실행:

```bash
uvicorn analyzer.main:app_factory --factory --port 8001
```

### 3. watcher

watcher는 서버별 YAML 설정 파일이 아니라, **watcher 프로세스 전체가 공유하는 설정 파일 1개**를 인자로 받습니다. 감시할 서버 목록 자체는 dashboard에서 관리합니다.

`watcher-config.yaml` 예시:

```yaml
registry_url: http://localhost:8000/api/servers
analyzer_endpoint: http://localhost:8001/api/errors
api_key_env: WATCHER_ANALYZER_API_KEY       # 값은 analyzer의 ANALYZER_API_KEY와 동일해야 함
registry_api_key_env: WATCHER_REGISTRY_API_KEY  # 값은 dashboard의 DASHBOARD_API_KEY와 동일해야 함
# queue_dir: watcher/.queue          (선택, 기본값)
# registry_poll_interval: 30         (선택, 초 단위 기본값)
# log_poll_interval: 15              (선택, 초 단위 기본값)
```

실행 전에 위 YAML에서 지정한 이름의 환경변수도 실제로 설정해야 합니다:

```bash
export WATCHER_ANALYZER_API_KEY=<analyzer의 ANALYZER_API_KEY와 동일한 값>
export WATCHER_REGISTRY_API_KEY=<dashboard의 DASHBOARD_API_KEY와 동일한 값>
python3 -m watcher.main watcher-config.yaml
```

감시할 서버는 dashboard 화면(`/servers`)에서 등록합니다 — server_id, host, port, username, SSH 개인키 경로, 로그 파일 경로, 로그 포맷(기본/커스텀 정규식)을 입력하면 watcher가 30초 내로 자동으로 감시를 시작합니다.

## 테스트

```bash
pytest
```

패키지별로만 실행하려면:

```bash
pytest tests/watcher/
pytest tests/dashboard/
pytest tests/analyzer/
```

## 프로젝트 구조

```
watcher/       SSH 기반 원격 로그 감시 + 에러 추출
analyzer/      OpenAI 분석 + Redis 중복 제거 + Slack 알림
dashboard/     SQLite 저장 + Slack 로그인 + FastAPI 화면
tests/         패키지별 테스트 (watcher/, dashboard/, analyzer/)
docs/superpowers/
  specs/       설계 문서 (브레인스토밍 결과)
  plans/       구현 계획
.claude/memory/  에이전트 간 API 계약 문서
```

## 참고

이 프로젝트는 학습/실습용 토이 프로젝트로, 아래는 의도적으로 범위 밖입니다:

- Slack/dashboard 전송 실패에 대한 재시도 큐(watcher만 자체 재시도 큐를 가지고 있음)
- 대시보드 내 사용자 관리 화면 (허용 이메일은 환경변수로만 관리)
- Redis/OpenAI/Slack 계정 자체의 발급 절차
