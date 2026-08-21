# Security Policy

비밀정보·Telegram 승인·Toss 주문·SQLite·GitHub Actions·Oracle에 적용하는 전체 기준은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.

- API 키, Telegram 토큰, SSH 개인키, 전체 계좌번호와 인증 헤더를 커밋하거나 공개 Issue에 쓰지 않습니다.
- 공개 Markdown에는 서버 절대경로·OS 사용자명·서비스 실명·backup/snapshot 파일명·host 식별자와 일회성 실행 ID를 쓰지 않습니다.
- 저장소는 public이므로 커밋된 전략·운영 구조는 공개 정보로 간주합니다. 비밀이어야 하는 값은 코드가 아니라 Environment Secret과 Oracle `shared/.env`에만 둡니다.
- `main`은 branch protection/ruleset, PR, Quality Gate와 Security 검사를 통과한 변경만 허용합니다.
- Oracle SSH는 검증된 host key를 `known_hosts`에 고정하고 `accept-new`를 사용하지 않습니다.
- 기본 운영은 forced dry-run이며 live 전환에는 별도 명시적 승인과 preflight가 필요합니다.
- forced dry-run의 모의원장과 `/account`의 실제 Toss read-only 조회는 별개이며 어느 쪽도 다른 쪽의 수량을 자동 채택하지 않습니다.
- 모든 위험증가 BUY는 만료되는 2단계 승인을 거치고, 최초진입 단계 버튼도 현재 DB 단계와 다르면 거부합니다.
- 불명확한 주문, 원장 불일치, 위험축소 미완료는 SAFE_MODE로 신규 BUY를 막습니다.

보안 문제가 의심되면 live를 잠그고 노출된 자격증명을 폐기·재발급한 뒤 저장소 소유자에게 비공개로 알립니다.
