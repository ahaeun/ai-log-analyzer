# dashboard 설계 문서

- 작성일: 2026-08-06
- 담당 에이전트: dashboard
- 담당 경로: `dashboard/`
- 프로젝트: ai-log-analyzer (python_log 토이프로젝트)

## 1. 배경 및 전체 파이프라인에서의 위치

```
watcher (SSH 중앙집중식, 완료) → analyzer (미구현) → dashboard (이 문서)
```

watcher는 SSH로 원격 서버 로그를 감시하며, dashboard에 등록된 서버 목록(`GET /api/servers`)을 폴링해 감시 대상을 동적으로 결정한다(계약: `.claude/memory/log-watcher/server-registry-api-contract.md`). analyzer는 아직 구현되지 않았으므로, dashboard가 analyzer로부터 분석 결과를 받는 API 계약(`POST /api/errors`)을 이번에 정의하고 문서화해, analyzer 하위 프로젝트가 나중에 그 계약을 구현하도록 한다.

dashboard는 (1) SSH 감시 대상 서버를 등록/수정/삭제하는 화면과 API, (2) analyzer가 분석한 에러 이력을 저장하고 조회하는 화면과 API, (3) TailAdmin 스타일의 시각적 홈 대시보드, (4) Slack 로그인 기반 접근 제어를 제공한다.

## 2. 전체 아키텍처

```
[FastAPI 앱: dashboard/]
├── 인증: Slack Sign in with Slack (OpenID Connect)
│     로그인 → Slack 인가 화면 → 콜백에서 workspace/email 검증 → 세션 쿠키 발급
├── 데이터: SQLite (servers 테이블 + errors 테이블)
├── API (기계 간 통신, X-API-Key 헤더)
│     - GET  /api/servers   → watcher가 폴링
│     - POST /api/errors    → analyzer가 분석 결과 저장
├── 화면 (Slack 세션 쿠키, Jinja2 + Tailwind CDN + Chart.js CDN)
│     - GET  /login, GET /auth/slack/callback, POST /logout
│     - GET  /                             대시보드 홈
│     - GET  /servers, POST /servers,
│       POST /servers/{server_id}/edit,
│       POST /servers/{server_id}/delete   서버 관리
│     - GET  /errors                       에러 이력 (쿼리파라미터 필터)
```

## 3. 데이터 모델 (SQLite)

```sql
CREATE TABLE servers (
    server_id      TEXT PRIMARY KEY,
    host           TEXT NOT NULL,
    port           INTEGER NOT NULL DEFAULT 22,
    username       TEXT NOT NULL,
    ssh_key_path   TEXT NOT NULL,
    log_path       TEXT NOT NULL,
    format         TEXT NOT NULL CHECK (format IN ('default', 'custom')),
    custom_pattern TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE errors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id    TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    log_level    TEXT NOT NULL,
    error_type   TEXT NOT NULL,
    message      TEXT NOT NULL,
    stack_trace  TEXT NOT NULL,
    raw_log      TEXT NOT NULL,
    ai_analysis  TEXT,
    notified     INTEGER NOT NULL DEFAULT 0,
    notified_at  TEXT,
    received_at  TEXT NOT NULL
);
```

`errors.server_id`는 `servers.server_id`를 참조하지만 FK 제약은 걸지 않는다 — 서버가 레지스트리에서 삭제돼도 과거 에러 이력은 보존해야 하기 때문이다. 앱 시작 시 `CREATE TABLE IF NOT EXISTS`로 자동 초기화한다.

`servers` 테이블의 `server_id`/`host`/`port`/`username`/`ssh_key_path`/`log_path`/`format`/`custom_pattern` 필드는 watcher의 `ServerEntry`와 1:1로 대응하며, `custom_pattern` 유효성 검사(정규식 컴파일 가능 여부 + `timestamp`/`level`/`message` named group 필수)는 watcher의 `ServerEntry.__post_init__` 로직을 그대로 재사용한다.

## 4. API 엔드포인트

### 4.1 기계 간 통신 (헤더 `X-API-Key`, 환경변수 `DASHBOARD_API_KEY`와 일치해야 함)

```
GET  /api/servers   → 200, JSON 배열 (계약: server-registry-api-contract.md와 동일 스키마)
POST /api/errors    → 200, 에러 1건 저장
```

`POST /api/errors` 요청 바디:
```json
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
`ai_analysis`, `notified`(기본 `false`), `notified_at`은 선택 필드다. 나머지 7개 필드는 필수이며 `watcher.models.ErrorEvent`와 동일하다.

`X-API-Key`가 없거나 `DASHBOARD_API_KEY`와 일치하지 않으면 401을 반환한다.

### 4.2 브라우저 화면 (Slack 로그인 세션 쿠키)

```
GET  /login                        Slack 로그인 버튼
GET  /auth/slack/callback          OIDC 콜백 → 세션 발급
POST /logout                       세션 종료

GET  /                             대시보드 홈
GET  /servers                      서버 목록
POST /servers                      서버 등록 (폼)
GET  /servers/{server_id}/edit      서버 수정 폼 (기존 값 채워서 표시)
POST /servers/{server_id}/edit     서버 수정 저장
POST /servers/{server_id}/delete   서버 삭제

GET  /errors                       에러 이력 (쿼리파라미터: server_id, from, to, error_type)
```

`/login`을 제외한 모든 브라우저 라우트는 세션이 없으면 `/login`으로 리다이렉트한다. 서버관리/에러이력 화면은 별도 JS 없이 일반 HTML 폼 제출(Post/Redirect/Get)과 쿼리파라미터 기반 GET으로 동작한다.

## 5. 인증 흐름 (Slack Sign in with Slack / OpenID Connect)

환경변수: `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_TEAM_ID`(허용 워크스페이스 ID), `DASHBOARD_ALLOWED_EMAILS`(쉼표로 구분된 허용 이메일 목록), `DASHBOARD_SESSION_SECRET`(세션 쿠키 서명 키), `DASHBOARD_API_KEY`(기계 간 통신용).

1. `GET /login`: "Slack으로 로그인" 버튼 → `https://slack.com/openid/connect/authorize?client_id=...&scope=openid,email,profile&redirect_uri=.../auth/slack/callback&state=<CSRF 랜덤값>` 로 이동. `state`는 임시 서명 쿠키에 저장한다.
2. `GET /auth/slack/callback?code=&state=`: `state` 검증 → `code`를 `https://slack.com/api/openid.connect.token`에서 토큰으로 교환 → `https://slack.com/api/openid.connect.userInfo`로 이메일/팀ID 조회.
3. 팀ID가 `SLACK_TEAM_ID`와 다르면 403. 이메일이 `DASHBOARD_ALLOWED_EMAILS`에 없으면 403.
4. 통과 시 `{"email": ..., "name": ...}`을 Starlette `SessionMiddleware`(서명된 쿠키, `DASHBOARD_SESSION_SECRET`)에 저장하고 `/`로 리다이렉트.
5. `POST /logout`: 세션을 지우고 `/login`으로 리다이렉트.

모든 보호된 라우트는 FastAPI 의존성(`Depends`)으로 세션 존재를 확인한다.

## 6. 화면 구성 (TailAdmin 스타일)

공통 레이아웃: 흰색 사이드바(로고 + 대시보드/서버관리/에러이력 메뉴) + 상단바(로그인 사용자 이메일 + 로그아웃 버튼) + 메인 영역. Tailwind CSS(CDN)와 Chart.js(CDN)로 참고 이미지의 둥근 카드·옅은 그림자·파란색 포인트 컬러를 재현한다.

- **`/login`**: 중앙 카드에 "Slack으로 로그인" 버튼만 표시.
- **`/` (홈)**: 통계 카드 3개(등록된 서버 수 / 오늘 발생 에러 수 / 오늘 알림 발송 수) + 최근 7일 에러 추이 라인 차트 + 최근 에러 5건 미리보기 테이블.
- **`/servers`**: 서버 목록 테이블(server_id/host/format/등록일) + "서버 추가" 폼 + 행마다 수정/삭제 버튼.
- **`/errors`**: 상단 필터 폼(서버 선택 드롭다운, 기간 date input 2개, 에러타입 텍스트 검색) + 결과 테이블(시각/서버/타입/메시지/알림여부) + 간단한 offset 기반 페이지네이션.

## 7. 에러 처리

- Slack OAuth 실패(코드 교환 실패, `state` 불일치) → `/login`으로 리다이렉트 + 에러 메시지 표시.
- 워크스페이스 불일치/이메일 미허용 → 403 페이지("접근 권한이 없습니다"), 세션은 발급되지 않는다.
- `X-API-Key` 없음/불일치 → 401.
- `POST /api/errors` 필수 필드 누락 → 422 (FastAPI/Pydantic 기본 검증).
- 서버 등록/수정 폼에서 `format: custom`인데 `custom_pattern`이 없거나 유효하지 않으면(정규식 오류, named group 누락) 폼에 에러 메시지를 표시하고 저장을 거부한다.
- SQLite 파일이 없으면 앱 시작 시 자동으로 테이블을 생성한다.

## 8. 테스트 전략

- DB 레이어: 임시 SQLite 파일(`tmp_path`)로 서버 추가/수정/삭제/조회, 에러 저장/필터 조회를 단위 테스트.
- API: FastAPI `TestClient`로 `/api/servers`, `/api/errors`의 정상/인증 실패(`X-API-Key` 없음/오류) 케이스를 테스트.
- Slack OIDC: `requests`를 mock해 토큰 교환/유저인포 흐름과 워크스페이스·이메일 검증 로직을 단위 테스트 (실제 Slack API 호출 없음).
- 보호된 화면: 세션 없이 접근 시 `/login`으로 리다이렉트되는지 테스트.
- 서버 등록/수정 폼: 잘못된 `custom_pattern`(정규식 오류, named group 누락)이 거부되는지 테스트 — watcher의 검증 로직과 동일한 케이스를 재사용.

## 9. 범위 밖

- analyzer의 실제 구현(OpenAI 분석, Redis 중복 제거, Slack 알림 전송)은 별도 하위 프로젝트다. 이 문서는 analyzer가 호출할 `POST /api/errors` 계약만 정의한다.
- 세션 저장소는 서명된 쿠키만 사용한다(서버 측 세션 저장소/DB 세션 테이블 없음) — 토이 프로젝트 규모에 적합하다.
- 사용자 관리 화면(허용 이메일을 UI에서 추가/삭제)은 두지 않는다 — 허용 목록은 환경변수로만 관리한다.

## 10. 다음 단계

이 설계가 승인되면 `.claude/memory/dashboard/`에 `POST /api/errors` 계약(9번 범위 밖에서 명시한 대로, analyzer가 참고할 스키마)을 문서화하고, writing-plans 스킬로 구현 계획을 작성한다.
