# Security Policy

## 비밀정보

API 키, Telegram 토큰, SSH 개인키, GitHub token을 이 저장소에 커밋하지 마세요.
`.env`와 런타임 데이터는 `.gitignore`에 포함되어 있으며 서버 `.env`는 `0600` 권한으로
관리합니다. 로그와 Telegram 메시지에 비밀값을 출력하지 않습니다.

Git HTTPS remote는 credential이 없는 URL을 사용합니다.

```text
https://github.com/hwanff11/JD_HOLDINGS.git
```

과거 remote URL에 token이 포함된 적이 있다면 URL을 바꾸는 것만으로는 충분하지
않습니다. GitHub에서 해당 token을 즉시 폐기하고 새 credential을 발급해야 합니다.

## 주문 통제

- 기본 모드는 dry-run입니다.
- live는 모드와 별도 확인 문구가 동시에 일치해야 합니다.
- Telegram 개인 Chat ID 정확히 한 명만 허용합니다.
- 매수마다 만료되는 2단계 승인이 필요합니다.
- 불명확한 주문, 잔고 불일치, 미체결 주문 불일치는 신규 매수를 중단합니다.

보안 문제는 공개 Issue에 토큰이나 계좌정보를 남기지 말고 저장소 소유자에게 비공개로
전달하세요.
