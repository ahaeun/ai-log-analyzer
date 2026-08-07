# 서버 레지스트리 API 계약 (dashboard가 구현, watcher가 호출)

- 작성일: 2026-08-06
- 관련 설계 문서: docs/superpowers/specs/2026-08-06-log-watcher-ssh-redesign.md
- 관련 구현: watcher/registry_client.py의 fetch_servers()

## 결론 요약
watcher는 30초(기본값)마다 아래 API를 호출해 현재 감시해야 할 서버 목록을 가져온다.
dashboard는 이 스키마로 응답하는 GET 엔드포인트를 구현해야 한다.

## API

```
GET {registry_url}
Header: X-API-Key: <registry_api_key_env가 가리키는 환경변수 값>
→ 200 OK
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

## 필드 설명
- `format`: `"default"`(표준 logback 패턴) 또는 `"custom"`(정규식 사용)
- `custom_pattern`: `format`이 `"custom"`일 때만 필수. named group으로 `timestamp`/`level`/`message`를 최소 포함해야 한다.
- `ssh_key_path`: watcher가 실행되는 중앙 서버 파일시스템 상의 개인키 경로. 키 파일 자체는 이 API로 전달되지 않는다 — dashboard는 경로 문자열만 저장/반환한다.

## watcher 쪽 동작
- 이 목록에 새로 나타난 `server_id`는 자동으로 SSH 감시가 시작된다.
- 이 목록에서 사라진 `server_id`는 자동으로 감시가 중단된다.
- 개별 항목이 유효하지 않으면(정규식 오류, 필수 필드 누락 등) 그 항목만 건너뛰고 나머지는 정상 처리한다.
- 이 API 자체가 응답하지 않으면 watcher는 마지막으로 알고 있던 목록을 유지하고 다음 주기에 재시도한다 (전체 프로세스가 멈추지 않는다).
