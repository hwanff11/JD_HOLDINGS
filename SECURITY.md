# Security Policy

비밀정보·Telegram 승인·Toss 주문·SQLite·GitHub Actions·Oracle에 적용하는 전체 기준은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.

- API 키, Telegram 토큰, SSH 개인키, 전체 계좌번호와 인증 헤더를 커밋하거나 공개 Issue에 쓰지 않습니다.
- 기본 운영은 forced dry-run이며 live 전환에는 별도 명시적 승인이 필요합니다.
- 모든 위험증가 BUY는 만료되는 2단계 승인을 거칩니다.
- 불명확한 주문, 원장 불일치, 위험축소 미완료는 SAFE_MODE로 신규 BUY를 막습니다.

보안 문제가 의심되면 live를 잠그고 노출된 자격증명을 폐기·재발급한 뒤 저장소 소유자에게 비공개로 알립니다.
