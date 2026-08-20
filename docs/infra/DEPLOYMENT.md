# Oracle 배포·검증·롤백 가이드

이 문서는 버전과 무관한 Oracle 운영 절차만 소유합니다. 배포할 전략 계약은 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)와 [`../../strategy.yaml`](../../strategy.yaml), 기대 SHA·현재 운용 모드는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)에서 확인합니다.

현재 보안 계약이 forced dry-run과 live hard lock을 요구하는 동안 배포는 이를 완화할 수 없습니다. 전략 버전별 수치와 완료된 마이그레이션 기록은 이 문서에 누적하지 않습니다.

## 1. 서버 구조

기본 경로는 `/home/ubuntu/JD_HOLDINGS`입니다.

- DB: `shared/data/jdss.db`
- 로그: `shared/logs/jdss.log`
- 시세 캐시: `shared/data/cache`
- 비밀정보: `shared/.env`
- 현재 코드: `current` 심볼릭 링크
- commit별 코드: `releases/<commit-sha>`

`.env`는 `0600`, systemd는 `UMask=0077`을 유지합니다. forced dry-run 계약에서는 다음 값을 배포 전후 모두 확인합니다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

Oracle 공인 IP는 Toss Securities OpenAPI 허용 IP에 등록하되 자격증명은 저장소에 두지 않습니다.

## 2. 배포 전 게이트

1. 원격 최신 `main`과 배포 대상 SHA가 정확히 일치하는지 확인합니다.
2. 작업트리에 미커밋 변경이 없고 배포 대상 PR의 필수 CI가 성공했는지 확인합니다.
3. `jdss validate-config`, Ruff와 pytest를 통과합니다.
4. strategy ID, config/package version, live 잠금과 자금·주문 안전 불변식을 검증합니다.
5. DB 스키마 또는 전략 세대가 달라지면 호환성 테스트, 복구 가능한 백업과 명시적 migration plan이 있어야 합니다.
6. 환경파일·shared DB·로그 권한과 필요한 Secret이 없으면 배포를 중단합니다.

전략 수치를 이 체크리스트에 복사하지 않습니다. 자동 검증은 `strategy.yaml`과 공식 사양을 직접 읽어야 합니다.

## 3. 배포 경로

표준 자동 배포는 저장소의 `deploy.sh`를 사용합니다.

```bash
env -u GITHUB_TOKEN ./deploy.sh
```

GitHub Actions를 사용할 때도 **Deploy Oracle Dry Run** workflow가 최신 `main`만 배포하도록 유지합니다. 일반 순서는 다음과 같습니다.

1. 정확한 최신 `main` 재확인
2. Python 환경 설치와 필수 검증
3. commit SHA별 release directory 생성
4. shared 환경·DB·로그 연결과 권한 확인
5. forced dry-run 환경 재확인
6. `current` 링크 전환과 systemd 재시작
7. config validation, 서비스 상태와 read-only smoke 확인

ChatOps를 사용한다면 저장소 소유자, 허용된 제목 접두어, GitHub Environment 권한을 모두 검증합니다. 오래된 SHA를 지정해 최신 `main` 검사를 우회하지 않습니다.

## 4. DB 마이그레이션과 최초 적용

배포 스크립트가 과거 릴리즈의 일회성 DB 초기화 규칙을 새 버전에 재사용해서는 안 됩니다.

- 변경 전 SQLite 전체와 WAL 관련 파일을 복구 가능한 이름으로 백업합니다.
- 기존 세대, schema version, 열린 주문, 부분체결, UNKNOWN, legacy position/TP 상태를 검사합니다.
- 기존 DB 호환성 또는 변환 규칙을 테스트로 증명하지 못하면 서비스를 시작하지 않습니다.
- 실제 거래원장은 자동 삭제·초기화하지 않습니다.
- 완료된 전환의 결과만 [`../HISTORY.md`](../HISTORY.md)에 요약하고, 현재 배포 가이드에는 재사용 가능한 절차만 남깁니다.

처음 전략을 계좌에 적용하거나 관리 티커가 바뀌는 경우에는 실제 Toss 보유수량·열린 주문·주문가능금액과 개인 동일 티커 혼합 여부도 별도 preflight로 확인합니다. 현재 live hard lock이 있는 동안 이 preflight는 실거래 활성화 명령이 아닙니다.

## 5. forced dry-run과 Toss 조회 경계

- 서비스의 dry-run 주문·보유수량·열린 주문은 SQLite와 모의 브로커 상태입니다.
- `/account`, 계좌 요약과 `toss-smoke`는 실제 Toss를 조회만 하는 별도 경로입니다.
- read-only smoke 성공은 인증·시세·장상태 조회가 된다는 뜻이지, dry-run 원장과 실제 Toss 수량이 자동 reconciliation됐거나 실주문이 검증됐다는 뜻이 아닙니다.
- Toss 조회 결과를 dry-run 원장에 자동 채택하거나 개인 보유분을 JDSS 물량으로 간주하지 않습니다.

이 구분은 배포 보고와 Telegram 안내에도 같은 용어로 표시합니다.

## 6. 재시작과 SAFE_MODE

Dry-run 재시작은 SQLite 원장으로 증명되는 보유수량과 열린 모의 주문만 복원합니다.

- `UNKNOWN` 또는 재시작 시점 `PARTIAL_FILLED`를 성공으로 추정하지 않습니다.
- DB에 broker order ID가 없거나 모의 브로커에서 찾지 못한 열린 주문은 SAFE_MODE 사유입니다.
- 목표 변경 전 기존 주문을 취소·정산하지 못하거나 위험축소 SELL이 끝나지 않으면 신규 BUY를 차단합니다.
- 시작 reconciliation이 실패해도 프로세스가 살아 있다는 이유만으로 정상 배포로 판정하지 않습니다.

실제 systemd restart/recovery 검증은 시장 세션과 운영 안전규칙을 따릅니다. 장중에 재시작을 생략했다면 그 사실과 남은 검증을 `CURRENT_WORK.md`에 기록합니다.

## 7. 배포 후 검증

- 서버 `current`가 기대 SHA인지 확인
- strategy/config/package 일치 확인
- forced dry-run·빈 live 확인값·애플리케이션 hard lock 확인
- service active와 최근 오류 로그 확인
- config validation
- Toss 인증·시세·시장 세션 read-only smoke
- SQLite/모의 브로커 reconciliation과 SAFE_MODE 확인
- Telegram `/ping`, `/portfolio`, `/account`, `/order`, `/errors`의 데이터 출처 표시 확인
- 시장이 허용하면 restart/recovery 확인

runtime verifier와 ChatOps 이름이 버전에 종속된다면 현재 workflow와 `CURRENT_WORK.md`에서 정확한 이름을 확인하고, 문서에 옛 이름을 영구 고정하지 않습니다.

## 8. 롤백

1. 장애 시 신규 BUY를 차단하고 forced dry-run/live 잠금을 재확인합니다.
2. 마지막 정상 release directory, DB 백업, 당시 전략 세대와 schema를 확인합니다.
3. 코드만 되돌려도 DB가 호환되는지 먼저 검증합니다.
4. 원장을 수동으로 맞추거나 불명확한 주문을 성공 처리하지 않습니다.
5. rollback 뒤에도 config, 서비스, reconciliation, read-only smoke와 재시작을 다시 검증합니다.

## 9. 운영 원칙

- production 전략 승격, Oracle 배포와 live 활성화는 서로 다른 결정입니다.
- Toss read-only smoke는 주문을 만들지 않습니다.
- 배포 성공은 서비스 active 하나가 아니라 SHA·설정·잠금·원장·조회 경계까지 모두 충족해야 합니다.
- 버전 전용 ChatOps workflow는 재실행과 운영 혼선을 막기 위해 릴리즈가 끝난 뒤 상시 canonical workflow에 남기지 않습니다.
