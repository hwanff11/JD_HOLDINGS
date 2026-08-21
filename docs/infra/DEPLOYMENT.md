# Oracle 배포·검증·롤백 가이드

이 문서는 버전과 무관한 Oracle 운영 절차만 소유합니다. 배포할 전략 계약은 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)와 [`../../strategy.yaml`](../../strategy.yaml), 현재 운용 상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)에서 확인합니다.

현재 보안 계약이 forced dry-run과 live hard lock을 요구하는 동안 배포는 이를 완화할 수 없습니다. 전략 버전별 수치와 완료된 일회성 마이그레이션 기록은 이 문서에 누적하지 않습니다.

## 1. 서버 구조

기본 운영 경로와 서비스 식별자는 보호된 배포 설정에서 관리합니다. 공개 문서에는 실제 서버의 절대 경로·OS 사용자명·서비스 실명·백업 파일명을 기록하지 않습니다.

- 현재 코드: `current` 심볼릭 링크
- commit별 release: `releases/<commit-sha>`
- release별 가상환경: `releases/<commit-sha>/.venv`
- DB: `shared/data/jdss.db`
- DB 배포 백업: `shared/backups/`
- 로그: `shared/logs/jdss.log`
- 시세 캐시: `shared/data/cache`
- 비밀정보: `shared/.env`
- systemd: 보호된 배포 설정에 지정된 서비스

이전 운영 대상의 식별자는 비공개 운영 기록에만 보존하며 새 배포에서 재사용하지 않습니다.

`.env`는 `0600`, systemd는 `UMask=0077`을 유지합니다. forced dry-run 계약에서는 다음 값을 배포 전후 모두 확인합니다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

## 2. SSH host key 검증

배포·runtime verifier는 `StrictHostKeyChecking=yes`를 사용합니다. 새 GitHub runner에서 `accept-new` 또는 즉석 `ssh-keyscan` 결과를 신뢰하지 않습니다.

1. Oracle 콘솔, 기존에 신뢰된 관리자 PC 등 별도 신뢰 경로에서 서버 SSH public host key를 확인합니다.
2. GitHub `oracle-dry-run` Environment secret `ORACLE_SSH_KNOWN_HOSTS`에 OpenSSH `known_hosts` 형식의 확인된 값을 저장합니다.
3. Actions는 그 값을 `~/.ssh/known_hosts`에 0600으로 기록하고, 값이 없으면 배포를 중단합니다.
4. 로컬 `deploy.sh`는 `SSH_KNOWN_HOSTS_PATH`가 반드시 필요하며, 해당 파일을 그대로 `UserKnownHostsFile`로 사용합니다.

서버 host key가 실제로 변경됐다면 원인을 확인하기 전까지 배포하지 않습니다.

## 3. 배포 전 게이트

1. 원격 최신 `main`과 배포 대상 SHA가 정확히 일치하는지 확인합니다.
2. 작업트리에 미커밋 변경이 없고 배포 대상 PR의 필수 CI가 성공했는지 확인합니다.
3. `jdss validate-config`, Ruff와 pytest를 통과합니다.
4. strategy ID, config/package version, live 잠금과 자금·주문 안전 불변식을 검증합니다.
5. 표준 배포는 **같은 config version**에서만 허용합니다. config version이 달라지면 별도 migration plan·DB 호환성 테스트·복구 계획 없이 배포하지 않습니다.
6. 환경파일·shared DB·로그 권한과 필요한 Secret이 없으면 배포를 중단합니다.
7. SSH host key가 검증된 `known_hosts`와 일치해야 합니다.

## 4. rollback-safe 표준 배포

표준 자동 배포는 저장소의 `deploy.sh`를 사용합니다.

```bash
env -u GITHUB_TOKEN ./deploy.sh
```

GitHub Actions의 **Deploy Oracle Dry Run**도 같은 `deploy.sh`를 호출합니다. 저장소 소유자는 Actions 화면의 버튼을 누르지 않아도 제목이 `[deploy-oracle-dry-run]`으로 시작하는 issue를 만들어 owner-only ChatOps로 실행할 수 있습니다. ChatGPT는 사용자가 배포를 승인했고 GitHub 연결 권한이 있을 때 이 issue를 생성하고 run 결과를 끝까지 확인할 수 있습니다.

이 자동화는 임의 브랜치나 SHA를 입력받지 않고 실행 시점의 정확한 최신 `main`만 배포합니다. `ORACLE_SSH_KNOWN_HOSTS`를 포함한 Environment secret은 사전에 구성되어 있어야 하며, ChatGPT·Codex가 비밀값 자체를 알아내거나 복사할 필요는 없습니다. 배포 성공은 forced dry-run 운영 갱신일 뿐 live 활성화가 아닙니다.

표준 순서는 다음과 같습니다.

1. 정확한 최신 `main` 재확인
2. 새 release directory를 만들고 **release 내부 `.venv`에 의존성 설치**
3. 새 release의 config를 검증하되 기존 서비스는 계속 실행
4. 현재 서비스·current 링크·systemd unit 상태를 rollback용으로 기록
5. 서비스를 정지한 직후 SQLite `backup()` API로 `shared/backups/`에 일관된 DB snapshot 생성
6. 새 systemd unit 설치 및 `current`를 새 release로 atomic switch
7. 새 release의 `jdss init-db`·config validation 실행
8. 설정된 서비스 시작 후 forced dry-run·service active·Toss read-only smoke 확인
9. 모든 검증이 성공한 뒤 배포 완료

서비스 정지 이후 어떤 단계라도 실패하면 자동 rollback이 동작합니다.

- 새 서비스를 정지
- 직전 `current` 링크 복원
- 직전 systemd unit 복원
- 배포 직전 DB snapshot 복원
- daemon reload 후 직전 서비스 재시작
- rollback 서비스가 active인지 확인

rollback이 성공하지 못하면 즉시 신규 BUY를 금지하고 수동 복구 대상으로 취급합니다. 성공 여부를 추정하지 않습니다.

## 5. DB 마이그레이션

표준 배포는 config version 변경을 처리하지 않습니다. 버전·스키마·전략 세대가 바뀌는 작업은 별도 migration PR로 수행합니다.

- 변경 전 SQLite 전체를 복구 가능한 snapshot으로 백업
- 기존 세대, schema, 열린 주문, 부분체결, UNKNOWN, legacy position/TP 상태 검사
- 기존 DB 호환성 또는 변환 규칙을 테스트로 증명
- 실제 거래원장 자동 삭제·초기화 금지
- migration 실패 시 DB와 코드 양쪽을 함께 되돌릴 수 있어야 함
- 완료된 일회성 migration workflow/script는 상시 canonical workflow에서 제거하고 결과만 [`../HISTORY.md`](../HISTORY.md)에 요약

처음 전략을 계좌에 적용하거나 관리 티커가 바뀌는 경우에는 실제 Toss 보유수량·열린 주문·주문가능금액과 개인 동일 티커 혼합 여부도 별도 preflight로 확인합니다.

## 6. forced dry-run과 Toss 조회 경계

- 서비스의 dry-run 주문·보유수량·열린 주문은 SQLite와 모의 브로커 상태입니다.
- `/account`, 계좌 요약과 `toss-smoke`는 실제 Toss를 조회만 하는 별도 경로입니다.
- read-only smoke 성공은 인증·시세·장상태 조회가 된다는 뜻이지, dry-run 원장과 실제 Toss 수량이 자동 reconciliation됐거나 실주문이 검증됐다는 뜻이 아닙니다.
- Toss 조회 결과를 dry-run 원장에 자동 채택하거나 개인 보유분을 JDSS 물량으로 간주하지 않습니다.

## 7. 재시작과 SAFE_MODE

Dry-run 재시작은 SQLite 원장으로 증명되는 보유수량과 열린 모의 주문만 복원합니다.

- `UNKNOWN` 또는 재시작 시점 `PARTIAL_FILLED`를 성공으로 추정하지 않습니다.
- DB에 broker order ID가 없거나 모의 브로커에서 찾지 못한 열린 주문은 SAFE_MODE 사유입니다.
- 목표 변경 전 기존 주문을 취소·정산하지 못하거나 위험축소 SELL이 끝나지 않으면 신규 BUY를 차단합니다.
- 시작 reconciliation이 실패해도 프로세스가 살아 있다는 이유만으로 정상 배포로 판정하지 않습니다.

실제 systemd restart/recovery 검증은 시장 세션과 운영 안전규칙을 따릅니다. 장중에 재시작을 생략했다면 그 사실과 남은 검증을 `CURRENT_WORK.md`에 기록합니다.

## 8. 배포 후 검증

- 서버 `current`가 기대 SHA인지 확인
- `current/.venv/bin/jdss`와 `current/.venv/bin/jdss-bot` 존재 확인
- strategy/config/package 일치 확인
- forced dry-run·빈 live confirmation·애플리케이션 hard lock 확인
- service active와 최근 오류 로그 확인
- config validation과 SQLite init/호환성 확인
- Toss 인증·시세·시장 세션 read-only smoke
- SQLite/모의 브로커 reconciliation과 SAFE_MODE 확인
- Telegram `/ping`, `/help`, `/portfolio`, `/onboarding`, `/account`, `/order`, `/errors` 데이터 출처·잠금 표시 확인
- 시장이 허용하면 restart/recovery 확인

## 9. 롤백

자동 rollback을 통과했더라도 운영자가 다음을 확인합니다.

1. `current`가 직전 정상 release인지 확인
2. 직전 systemd unit과 서비스 active 확인
3. 복원된 DB snapshot의 config/schema 호환성 확인
4. 원장을 수동으로 맞추거나 불명확한 주문을 성공 처리하지 않음
5. reconciliation, read-only smoke, Telegram 상태를 다시 확인

## 10. GitHub Actions·branch protection

- `main`은 PR 기반 변경만 허용하고 직접 push·force push·branch delete를 보호합니다.
- Quality Gate와 Security를 필수 check로 지정합니다. 장기 전략 변경은 Backtest 검증도 병합 조건으로 추가합니다.
- GitHub Actions 기본 권한은 `contents: read`로 두고 필요한 workflow만 최소 권한을 추가합니다.
- Oracle 배포 workflow는 승인된 `oracle-dry-run` Environment secret만 사용합니다.
- 완료된 버전/마이그레이션 전용 workflow는 제거하여 재실행 표면을 줄입니다.

## 11. 운영 원칙

- production 전략 승격, Oracle 배포와 live 활성화는 서로 다른 결정입니다.
- Toss read-only smoke는 주문을 만들지 않습니다.
- 배포 성공은 service active 하나가 아니라 SHA·설정·잠금·원장·조회 경계·rollback 가능성까지 모두 충족해야 합니다.
- live hard lock 해제는 이 배포 문서만 수정해서 수행할 수 없습니다. 별도 preflight·코드·테스트·문서·명시적 승인이 필요합니다.
