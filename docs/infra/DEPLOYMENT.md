# Oracle 배포 가이드

Oracle 배포 대상은 검증된 최신 `main`의 **JDSS V3.2.2 forced dry-run 계약**입니다. 전략 수치는 루트 `strategy.yaml`과 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md), 현재 상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)를 기준으로 봅니다.

V3.2.2는 설정과 애플리케이션 양쪽에서 live 시작을 거부합니다.

## 1. 서버 구조

기본 경로는 `/home/ubuntu/JD_HOLDINGS`입니다.

- DB: `shared/data/jdss.db`
- 로그: `shared/logs/jdss.log`
- yfinance 캐시: `shared/data/cache`
- 비밀정보: `shared/.env`
- 현재 코드: `current` 심볼릭 링크
- commit별 코드: `releases/<commit-sha>`

`.env`는 `0600` 권한을 사용하고 다음을 강제합니다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

Oracle 공인 IP는 Toss Securities OpenAPI 허용 IP에 등록합니다.

## 2. GitHub Actions 배포

정식 경로는 **Deploy Oracle Dry Run** workflow입니다.

1. 실행 시점의 정확한 최신 `main` 확인
2. Python 3.12에서 Ruff·pytest·config validation
3. V3.2.2 frozen 계약 확인
   - `JDSS-3.2.2-RS6M-ONEWAY-HWM75`
   - `config_version: 3.2.2`
   - `total_capital: 50000`
   - HWM reinvestment 0.75
   - RS lookback 126
   - SOXL sleeve 0.50
   - JDSS overlay 0.05
   - SGOV OFF
   - `live_enabled: false`
4. commit SHA별 release directory 배포
5. 서버 `.env` forced dry-run 재확인
6. systemd 재시작
7. `jdss validate-config`
8. Toss read-only `toss-smoke`

ChatOps가 필요하면 저장소 소유자가 제목을 `[deploy-oracle-dry-run]`으로 시작하는 이슈를 생성합니다. 오래된 SHA 지정 우회배포는 허용하지 않습니다.

## 3. V3.1.1 → V3.2.2 최초 전환

V3.2.2는 V3.1.1과 원장 구조가 다릅니다. V3.1.1의 TQQQ/SOXL direct H40 포지션과 TP plan을 V3.2.2 allocation 원장으로 그대로 이어쓰지 않습니다.

배포 workflow가 서버 `.env`를 먼저 forced dry-run으로 잠근 상태에서 `deploy.sh`가 기존 SQLite 전체를 다음 형식으로 백업합니다.

`jdss.v322-migration.<old-version>.<timestamp>.db`

그 뒤 기존 dry-run SQLite를 제거하고 V3.2.2가 새 QQQ/TQQQ/SOXL allocation ledger를 생성합니다. 백업에는 기존 positions/orders/TP/events가 그대로 남습니다.

이 자동 초기화는 **forced dry-run 전환에만** 적용합니다. 향후 live가 별도 승인되는 경우 같은 방식으로 실제 거래원장을 자동 초기화해서는 안 됩니다.

## 4. V3.2.2 관리계좌

- 시작 위험원금: $50,000
- HWM75: 최고자산 증가분의 75%만 위험예산 확대에 반영
- 나머지 25% 이익은 JDSS 현금이지만 위험예산 확대에는 사용하지 않음
- 손실 시 개인 현금 자동보충 없음
- QQQ/TQQQ/SOXL allocation을 SQLite와 Toss 수량으로 reconciliation
- 같은 Toss 계좌의 개인 QQQ/TQQQ/SOXL 혼합보유 금지
- SGOV OFF

신규 BUY 상한은 HWM75 남은 위험예산, JDSS 실제 가용현금, Toss 주문가능금액 중 가장 작은 값입니다.

## 5. 재시작과 SAFE_MODE

Dry-run 재시작 시 SQLite allocation ledger와 열린 DRY 주문을 복원합니다. 다음은 SAFE_MODE 사유입니다.

- DB/Toss 수량 불일치
- 열린 local 주문과 broker 주문 불일치
- UNKNOWN 주문
- 위험축소 SELL 불완전
- V3.1.1 direct booster position/TP plan 잔존
- 비관리 개인 QQQ/TQQQ/SOXL 수량

시장 세션이 `closed`일 때만 runtime verifier가 실제 systemd restart 검증을 수행합니다. 장중에는 읽기 검증만 수행합니다.

## 6. 배포 후 검증

배포 뒤 **Verify Oracle V3.2.2 Runtime** workflow를 실행합니다.

- 서버 `current`가 기대 SHA인지 확인
- strategy/config 3.2.2 확인
- HWM75/RS6M/live OFF 확인
- `JDSS_TRADING_MODE=dry_run`
- service active 확인
- config validation
- Toss read-only smoke test
- closed market이면 restart/recovery 확인

ChatOps 이슈 제목은 `[verify-oracle-v322]`로 시작합니다.

## 7. 운영 원칙

- production 전략 승격과 live 활성화는 별도 결정입니다.
- 이번 v3.2.2 릴리즈는 live를 켜지 않습니다.
- Toss smoke test는 주문을 만들지 않습니다.
- rollback이 필요하면 과거 release directory와 migration backup을 근거로 판단하고 DB를 임의 수정하지 않습니다.
- 버전 전용 ChatOps workflow는 재실행과 운영 혼선을 막기 위해 릴리즈가 끝난 뒤 상시 canonical workflow에 남기지 않습니다.
