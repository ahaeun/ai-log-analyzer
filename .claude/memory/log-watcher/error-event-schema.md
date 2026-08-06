# 에러 이벤트 스키마 (log-watcher → analyzer)

- 작성일: 2026-08-05
- 관련 설계 문서: docs/superpowers/specs/2026-08-05-log-watcher-design.md
- 관련 구현: watcher/models.py의 ErrorEvent, watcher/sender.py가 이 스키마로 HTTP POST 전송

## 결론 요약
log-watcher는 아래 JSON 스키마로 에러 이벤트를 중앙 서버(analyzer)에 전송한다.
analyzer는 수집 API에서 이 필드를 그대로 받는다고 가정하고 설계해야 한다.

## 스키마

```json
{
  "server_id": "server-a",
  "timestamp": "2026-08-05T12:35:01+09:00",
  "log_level": "ERROR",
  "error_type": "java.lang.NullPointerException",
  "message": "Cannot invoke ...",
  "stack_trace": "at com.example...\n...",
  "raw_log": "원본 로그 라인 전체 (스택트레이스 포함)"
}
```

## 전송 방식
- HTTP POST, JSON body
- 헤더: `X-API-Key: <watcher 전체가 공유하는 WatcherConfig.api_key_env가 가리키는 환경변수 값>` (2026-08-06 SSH 재설계 이후 서버별이 아닌 watcher 프로세스 전체가 공유하는 하나의 키다)
- 전송 실패(네트워크 오류, timeout, 5xx) 시 워처가 로컬에서 재시도하므로, analyzer는 동일 이벤트가 지연되어 도착할 수 있음을 고려해야 한다 (중복 수신 자체는 없음 — 워처가 성공한 요청만 큐에서 제거).
