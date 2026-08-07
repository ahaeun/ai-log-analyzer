# ai-log-analyzer

Spring Boot 서버 로그에서 에러가 발생하면 자동으로 감지해 OpenAI로 원인을 분석하고, Redis로 중복 알림을 제거한 뒤 Slack으로 알림을 보내고, SQLite에 이력을 저장해 FastAPI 대시보드로 조회하는 토이 프로젝트입니다.

## 아키텍처

```
[모니터링 대상 Spring Boot 서버 여러 대]
        │  SSH로 원격 로그 파일을 tail — 서버에는 아무것도 설치하지 않음
        ▼
┌───────────────┐   POST /api/errors    ┌───────────────┐   POST /api/errors    ┌───────────────┐
│   watcher     │ ────(X-API-Key)────▶  │   analyzer    │ ────(X-API-Key)────▶  │   dashboard   │
│ SSH 원격 감시 │                       │ OpenAI 분석    │                       │ SQLite 저장   │
│ + 에러 추출   │                       │ Redis 중복제거 │                       │ Slack 로그인  │
│               │ ◀── GET /api/servers ─│ Slack 알림    │                       │ FastAPI 화면  │
└───────────────┘   (서버 등록 목록)    └───────────────┘                       └───────────────┘
```

| 컴포넌트 | 역할 |
|---|---|
| **watcher** | 중앙 프로세스 하나가 SSH로 여러 원격 서버의 로그 파일을 직접 tail합니다. 감시할 서버 목록은 dashboard의 `GET /api/servers`를 30초마다 폴링해 동적으로 갱신합니다(대시보드에서 서버를 등록/삭제하면 자동 반영). |
| **analyzer** | watcher가 보낸 에러를 받으면 즉시 `202`를 응답하고, 백그라운드에서 OpenAI로 원인을 분석하고, Redis로 10분 내 동일 에러(서버+타입+메시지) 중복 알림을 억제하고, Slack Webhook으로 알림을 보낸 뒤 dashboard에 저장을 위임합니다. |
| **dashboard** | 서버 등록 관리(등록/수정/삭제), 에러 이력 조회(필터+페이지네이션), Slack 로그인(워크스페이스+이메일 허용목록 제한), 통계 카드/추이 차트가 있는 홈 화면을 제공합니다. |

세 컴포넌트는 서로의 API 계약(스키마)에 맞춰 통신하며, 그 계약은 `.claude/memory/`에 문서화되어 있습니다.

## 빠른 시작 (Docker Compose)

dashboard + analyzer + Redis를 한 번에 띄우는 가장 쉬운 방법입니다. watcher는 실제 SSH 접속 대상 서버와 개인키가 있어야 의미가 있어서 컨테이너에는 포함하지 않았습니다.

```bash
cp .env.example .env
# .env를 열어 실제 값(OpenAI 키, Slack 정보 등)을 채워넣으세요.

docker compose up -d --build
```

- dashboard: http://localhost:8000 (SQLite는 `dashboard_data` named volume에 저장되어 컨테이너를 내렸다 올려도 유지됩니다)
- analyzer: http://localhost:8001
- Redis: localhost:6379

`.env`의 `DASHBOARD_API_KEY`/`ANALYZER_API_KEY`는 두 컨테이너 양쪽에 자동으로 전달되고, `REDIS_URL`/`DASHBOARD_URL`은 compose 네트워크 안에서 서비스 이름(`redis`, `dashboard`)으로 자동 연결되므로 따로 설정할 필요가 없습니다.

내리려면:

```bash
docker compose down        # 컨테이너만 정리 (데이터는 유지)
docker compose down -v     # 컨테이너 + SQLite 데이터까지 완전히 삭제
```

watcher는 이 상태에서 로컬로 실행하면 됩니다 — 포트가 호스트에 노출되어 있어 아래 [watcher 실행](#watcher)에서 `localhost:8000`/`localhost:8001`을 그대로 쓰면 됩니다.

## 직접 실행 (Docker 없이)

```bash
python3 -m pip install -r requirements.txt
```

### dashboard

| 환경변수 | 설명 |
|---|---|
| `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` | Slack OAuth 앱 자격증명 (Sign in with Slack) |
| `SLACK_TEAM_ID` | 로그인을 허용할 Slack 워크스페이스 ID |
| `DASHBOARD_ALLOWED_EMAILS` | 로그인을 허용할 이메일 목록 (쉼표로 구분) |
| `DASHBOARD_SESSION_SECRET` | 로그인 세션 쿠키 서명 키 |
| `DASHBOARD_API_KEY` | watcher/analyzer가 이 값을 `X-API-Key`로 보내야 함 |
| `DASHBOARD_DB_PATH` (선택) | SQLite 파일 경로, 기본값 `dashboard/data.db` |

```bash
uvicorn dashboard.main:app_factory --factory --port 8000
```

### analyzer

| 환경변수 | 설명 |
|---|---|
| `ANALYZER_API_KEY` | watcher가 이 값을 `X-API-Key`로 보내야 함 |
| `OPENAI_API_KEY` | OpenAI 에러 분석용 |
| `REDIS_URL` | 예: `redis://localhost:6379/0` |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `DASHBOARD_URL` | dashboard 서비스 베이스 URL, 예: `http://localhost:8000` |
| `DASHBOARD_API_KEY` | dashboard의 `DASHBOARD_API_KEY`와 동일한 값 |

```bash
uvicorn analyzer.main:app_factory --factory --port 8001
```

### watcher

watcher는 서버별 YAML 파일이 아니라, **프로세스 전체가 공유하는 설정 파일 1개**를 인자로 받습니다. 감시할 서버 목록 자체는 dashboard에서 관리합니다.

`watcher-config.yaml` 예시:

```yaml
registry_url: http://localhost:8000/api/servers
analyzer_endpoint: http://localhost:8001/api/errors
api_key_env: WATCHER_ANALYZER_API_KEY           # 값은 analyzer의 ANALYZER_API_KEY와 동일해야 함
registry_api_key_env: WATCHER_REGISTRY_API_KEY  # 값은 dashboard의 DASHBOARD_API_KEY와 동일해야 함
# queue_dir: watcher/.queue          (선택, 기본값)
# registry_poll_interval: 30         (선택, 초 단위 기본값)
# log_poll_interval: 15              (선택, 초 단위 기본값)
```

```bash
export WATCHER_ANALYZER_API_KEY=<analyzer의 ANALYZER_API_KEY와 동일한 값>
export WATCHER_REGISTRY_API_KEY=<dashboard의 DASHBOARD_API_KEY와 동일한 값>
python3 -m watcher.main watcher-config.yaml
```

감시할 서버는 dashboard 화면(`/servers`)에서 등록합니다 — server_id, host, port, username, SSH 개인키 경로, 로그 파일 경로, 로그 포맷(기본/커스텀 정규식)을 입력하면 watcher가 30초 내로 자동으로 감시를 시작합니다.

## 테스트

```bash
pytest                    # 전체
pytest tests/watcher/
pytest tests/dashboard/
pytest tests/analyzer/
```

## 프로젝트 구조

```
watcher/            SSH 기반 원격 로그 감시 + 에러 추출
analyzer/           OpenAI 분석 + Redis 중복 제거 + Slack 알림
dashboard/          SQLite 저장 + Slack 로그인 + FastAPI 화면
tests/              패키지별 테스트 (watcher/, dashboard/, analyzer/)
docs/superpowers/
  specs/            설계 문서 (브레인스토밍 결과)
  plans/            구현 계획
.claude/memory/     에이전트 간 API 계약 문서
Dockerfile          dashboard/analyzer 공용 이미지 (watcher는 컨테이너화하지 않음)
docker-compose.yml  dashboard + analyzer + Redis
.env.example        docker compose용 환경변수 템플릿
```

## 범위 밖

학습/실습용 토이 프로젝트로, 아래는 의도적으로 다루지 않습니다:

- Slack/dashboard 전송 실패에 대한 재시도 큐 (watcher만 자체 재시도 큐를 가지고 있음)
- 대시보드 내 사용자 관리 화면 (허용 이메일은 환경변수로만 관리)
- Redis/OpenAI/Slack 계정 자체의 발급 절차