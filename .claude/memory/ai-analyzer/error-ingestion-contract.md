# 에러 수신 API 계약 (watcher가 호출, analyzer가 구현)

- 작성일: 2026-08-07
- 관련 구현: analyzer/main.py의 POST /api/errors
- 관련 문서: .claude/memory/dashboard/error-ingestion-api-contract.md (analyzer -> dashboard 계약, 자매 문서)

## 결론 요약
watcher는 파싱한 에러 이벤트 1건마다 analyzer의 아래 API를 호출한다. analyzer는
요청을 받는 즉시 `202`를 응답하고, 실제 처리(OpenAI 분석 -> Redis 중복 제거 ->
Slack 알림 -> dashboard 저장)는 FastAPI `BackgroundTasks`로 응답 이후에 수행한다.

## API

```
POST /api/errors
Header: X-API-Key: <ANALYZER_API_KEY>
Content-Type: application/json

{
  "server_id": "server-a",
  "timestamp": "2026-08-06T12:35:01+09:00",
  "log_level": "ERROR",
  "error_type": "java.lang.NullPointerException",
  "message": "...",
  "stack_trace": "...",
  "raw_log": "..."
}
```

`server_id`~`raw_log` 7개 필드 모두 필수이며 `watcher.models.ErrorEvent`와 동일한
스키마다.

## 응답
- `202`: `{"status": "accepted"}` — 요청이 접수되어 백그라운드 처리가 예약됐다는
  뜻일 뿐, OpenAI 분석/Slack 알림/dashboard 저장이 실제로 성공했다는 보장은
  아니다. 이 셋 중 하나가 실패해도 `202` 응답 자체는 이미 나간 뒤이므로 watcher
  쪽에서 재시도할 방법이 없다 (watcher는 202를 받는 즉시 이벤트를 큐에서 뺀다).
  그래서 analyzer 내부적으로는:
  - Redis 연결/조회 실패 → "중복 아님"으로 간주하고 계속 진행
  - OpenAI 분석 실패 → `ai_analysis=None`으로 계속 진행
  - Slack/dashboard 호출 실패 → 예외를 던지지 않고 로그만 남기고 계속 진행
  - 그래도 예상치 못한 예외가 체인 어딘가에서 발생하면 `analyzer/processor.py`의
    최상위 `try/except`가 잡아 `logger.exception(...)`으로 기록하고 삼킨다
    (BackgroundTasks 콜백 밖으로 예외가 새어나가지 않도록 하는 최후 방어선).
- `401`: `X-API-Key` 없음/불일치
- `422`: 필수 필드 누락 (FastAPI 기본 검증 오류 형식)

## 실행 방법

```
uvicorn analyzer.main:app_factory --factory
```

필수 환경변수 6개 (`analyzer/config.py`의 `REQUIRED_ENV_VARS`):
- `ANALYZER_API_KEY` — watcher가 이 API를 호출할 때 쓰는 X-API-Key 값
- `OPENAI_API_KEY` — 에러 원인 분석용 OpenAI API 키
- `REDIS_URL` — 10분 중복 알림 억제용 Redis 접속 URL (예: `redis://localhost:6379/0`,
  스킴 누락 등 잘못된 URL이면 연결 실패와 동일하게 "중복 아님"으로 처리됨)
- `SLACK_WEBHOOK_URL` — 알림을 보낼 Slack Incoming Webhook URL
- `DASHBOARD_URL` — dashboard 서비스 베이스 URL (끝에 슬래시가 있어도 안전하게
  처리됨)
- `DASHBOARD_API_KEY` — dashboard의 POST /api/errors 호출용 X-API-Key 값
