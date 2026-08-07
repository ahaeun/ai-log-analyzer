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
