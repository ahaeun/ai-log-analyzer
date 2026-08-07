# analyzer 설계 문서

- 작성일: 2026-08-06
- 담당 에이전트: ai-analyzer
- 담당 경로: `analyzer/`
- 프로젝트: ai-log-analyzer (python_log 토이프로젝트)

## 1. 배경 및 전체 파이프라인에서의 위치

```
watcher (완료) → analyzer (이 문서) → dashboard (완료)
```

watcher는 이미 완성되어 있으며, 에러를 감지하면 `POST {analyzer_endpoint}`로 `ErrorEvent` 스키마(7개 필드: server_id, timestamp, log_level, error_type, message, stack_trace, raw_log)를 `X-API-Key` 헤더와 함께 전송한다(계약: `.claude/memory/log-watcher/error-event-schema.md`). dashboard도 이미 완성되어 있으며, `POST /api/errors`로 분석 결과를 받아 SQLite에 저장하는 계약이 이미 정의되어 있다(`.claude/memory/dashboard/error-ingestion-api-contract.md`).

analyzer는 이 둘을 잇는 마지막 조각으로, watcher로부터 에러를 받아 OpenAI로 분석하고, Redis로 중복 알림을 억제한 뒤, Slack Webhook으로 알림을 보내고, 최종적으로 dashboard에 저장을 위임한다.

## 2. 전체 아키텍처

```
[watcher] --POST /api/errors (X-API-Key: ANALYZER_API_KEY)--> [analyzer FastAPI]
                                                                      │
                                                              즉시 202 응답
                                                                      │
                                                    (FastAPI BackgroundTasks로 이어서 처리)
                                                                      │
                              ┌───────────────────────────────────────┼───────────────────────────────────────┐
                              ▼                                       ▼                                       │
                    Redis 중복 확인                            OpenAI 분석                                    │
                    (server_id+error_type+message               (실패 시 ai_analysis=None,                    │
                     해시 키, TTL 10분)                           예외를 던지지 않고 계속 진행)                  │
                              │                                       │                                       │
                              └───────────────────┬───────────────────┘                                       │
                                                   ▼                                                           │
                                     중복이 아니면 Slack Webhook 알림                                          │
                                                   │                                                           │
                                                   ▼                                                           │
                          dashboard로 POST /api/errors (ai_analysis + notified + notified_at 포함,
                          중복으로 판정돼 알림이 억제된 에러도 notified=false로 저장)
```

watcher는 analyzer에 보낸 요청에 대해 5초 안에 응답을 받지 못하면 실패로 간주하고 로컬 큐에 넣어 재시도한다(`watcher/sender.py`의 기존 동작). OpenAI 분석 + Redis + Slack + dashboard 호출을 모두 합치면 5초를 넘길 수 있으므로, analyzer의 수신 엔드포인트는 인증과 스키마 검증만 마치는 즉시 `202`를 반환하고, 나머지 처리는 FastAPI `BackgroundTasks`로 응답 이후에 수행한다. (watcher가 타임아웃으로 재전송하더라도, Redis 중복 확인 로직이 동일 이벤트의 중복 처리를 자연스럽게 걸러낸다.)

## 3. 모듈 구성

```
analyzer/
├── config.py            AnalyzerConfig — api_key(watcher 수신 인증), openai_api_key,
│                         redis_url, slack_webhook_url, dashboard_url, dashboard_api_key
├── openai_client.py      analyze_error(event) -> str | None (실패 시 None, 예외를 던지지 않음)
├── dedup.py              is_duplicate(redis_client, event) -> bool (SET NX + TTL 10분)
├── slack_client.py       send_notification(webhook_url, event, ai_analysis)
├── dashboard_client.py   store_error(config, event, ai_analysis, notified, notified_at)
├── processor.py          process_error(event, config) — 위 4개를 순서대로 조율하는 오케스트레이션
└── main.py               FastAPI 앱: POST /api/errors 수신 엔드포인트(X-API-Key 인증) → 202 + BackgroundTasks
```

`watcher.models.ErrorEvent`를 그대로 import해서 재사용한다 — dashboard가 `watcher.models.ServerEntry`의 검증 로직을 재사용한 것과 같은 패턴이며, watcher가 보내는 이벤트 스키마와 완전히 동일하므로 별도로 재정의하지 않는다.

## 4. 인프라 / 환경변수

이번에 저장소 루트에 `docker-compose.yml`을 새로 추가해 Redis를 로컬에 띄운다(`redis:7-alpine` 이미지, 기본 포트 6379).

환경변수:
- `ANALYZER_API_KEY` — watcher가 보내는 `X-API-Key`와 일치해야 함 (watcher의 `api_key_env`가 가리키는 환경변수 값과 동일하게 운영자가 맞춰줘야 한다)
- `OPENAI_API_KEY`
- `REDIS_URL` (예: `redis://localhost:6379/0`)
- `SLACK_WEBHOOK_URL`
- `DASHBOARD_URL` (dashboard의 `/api/errors` 엔드포인트 베이스 URL)
- `DASHBOARD_API_KEY` — dashboard의 `DASHBOARD_API_KEY`와 일치해야 함

## 5. 처리 흐름 상세

1. `POST /api/errors` 요청의 `X-API-Key`가 `ANALYZER_API_KEY`와 다르면 401, 필수 7개 필드가 없으면 422 (둘 다 백그라운드 진입 전 즉시 응답).
2. 검증을 통과하면 즉시 `202`를 반환하고 `process_error(event, config)`를 `BackgroundTasks`에 등록한다.
3. `process_error`:
   a. dedup 키 = `sha256(f"{server_id}:{error_type}:{message}")`. Redis에 이미 있으면 `is_duplicate=True`; 없으면 키를 TTL 10분으로 설정하고 `is_duplicate=False`.
   b. `analyze_error(event)` 호출 — OpenAI로 원인/해결방향을 요약. 실패(타임아웃/API 오류)해도 예외를 밖으로 던지지 않고 `None`을 반환해 흐름을 막지 않는다.
   c. `is_duplicate`가 `False`면 `send_notification(...)`으로 Slack에 알림을 보내고 `notified=True`, `notified_at=지금`. `True`(중복)면 Slack을 보내지 않고 `notified=False`, `notified_at=None`.
   d. `store_error(...)`로 dashboard의 `POST /api/errors`를 호출해 원본 이벤트 + `ai_analysis` + `notified`/`notified_at`을 저장한다 — 중복으로 판정된 에러도 저장한다(알림만 억제됐을 뿐 이력에서는 빠지지 않아야 한다).

## 6. 에러 처리

- `X-API-Key` 불일치/누락 → 401 (즉시)
- 필수 필드 누락 → 422 (FastAPI 기본 검증)
- OpenAI 실패 → `ai_analysis=None`으로 계속 진행
- Redis 연결 실패 → 중복 여부를 판단할 수 없으므로 안전하게 "중복 아님"으로 간주하고 계속 진행 (알림을 놓치는 것보다 중복 알림 쪽이 낫다)
- Slack 전송 실패 → 로그만 남기고 계속 진행 (재시도 큐는 이번 범위 밖)
- dashboard 저장 실패 → 로그만 남기고 계속 진행 (재시도 큐는 이번 범위 밖)

## 7. 테스트 전략

- `openai_client`/`slack_client`/`dashboard_client`: 각각 외부 호출(OpenAI SDK, `requests.post`)을 mock해서 성공/실패 케이스를 단위 테스트
- `dedup.py`: Redis 클라이언트를 mock(또는 `fakeredis`)해서 SET NX + TTL 동작과 연결 실패 시 "중복 아님"으로 안전하게 처리되는지 검증
- `processor.py`: 위 4개를 모두 mock해서 조율 순서(중복이면 Slack 스킵, OpenAI 실패해도 계속, 중복이어도 dashboard에는 저장 등)를 단위 테스트
- `main.py`: FastAPI `TestClient`로 인증 성공/실패, 202 응답, `BackgroundTasks`가 `process_error`를 정확한 인자로 호출하는지 검증

## 8. 범위 밖

- Slack/dashboard 전송 실패에 대한 재시도 큐(watcher의 `sender.py`처럼) — 이번 토이 프로젝트 범위에서는 로그만 남기고 넘어간다.
- Redis/OpenAI/Slack Webhook의 실제 계정·키 발급 — 사용자가 이미 보유한 값을 환경변수로 공급한다는 전제.
- watcher/dashboard 쪽 코드 변경 — 이미 정의된 계약을 그대로 소비/구현하며, 계약 자체를 바꾸지 않는다.

## 9. 다음 단계

이 설계가 승인되면 writing-plans 스킬로 구현 계획을 작성한다.
