# Oracle 배포 가이드

Oracle 배포 대상은 검증된 최신 `main`의 **JDSS V3.1.1 forced dry-run 계약**이다. 전략 수치는 루트 `strategy.yaml`과 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)를 따르며, 실제 배포 SHA와 현재 서버 상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)만 기준으로 확인한다.

V3.1.1은 애플리케이션과 설정 양쪽에서 live 시작을 거부한다.

## 1. 서버 기본 구조

기본 경로는 `/home/ubuntu/JD_HOLDINGS`다.

- DB: `shared/data/jdss.db`
- 로그: `shared/logs/jdss.log`
- yfinance 캐시: `shared/data/cache`
- 비밀정보: `shared/.env`
- 현재 코드: `current` 심볼릭 링크
- commit별 코드: `releases/<commit-sha>`

`.env`는 `0600` 권한을 사용하고 live 승격 전까지 다음을 고정한다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

Oracle 공인 IP는 Toss Securities OpenAPI 허용 IP에 등록한다.

## 2. 권장 배포 경로

권장 경로는 GitHub Actions **Deploy Oracle Dry Run**이다.

워크플로는 다음을 수행한다.

1. 실행 시점의 정확한 최신 `main` 확인
2. Python 3.12 환경에서 Ruff·pytest·config validation
3. V3.1.1 계약 확인
   - `JDSS-3.1.1-TWIN-H40-S3`
   - `config_version: 3.1.1`
   - `total_capital: 50000`
   - `capital_per_symbol: 20000`
   - `idle_cash.enabled: false`
   - `live_enabled: false`
4. commit별 릴리스 디렉터리 생성
5. 서버 `.env`를 forced dry-run으로 유지
6. systemd `jd_holdings_bot` 재시작
7. `jdss validate-config`
8. Toss 조회 전용 smoke test

ChatGPT 연결에서 직접 Actions dispatch 대신 ChatOps가 필요하면 저장소 소유자가 제목을 `[deploy-oracle-dry-run]`으로 시작하는 이슈를 생성한다. 오래된 SHA를 지정해 우회 배포하지 않는다.

## 3. 버전 변경 안전게이트

`config_version`이 달라지는 배포에서는 진행 중 거래에 새 자금계약을 중간 적용하지 않는다.

배포 스크립트는 기존 서비스를 정지한 뒤 DB를 확인하며 다음이 있으면 버전 전환을 중단하고 기존 서비스를 다시 시작한다.

- `positions`에 `EMPTY`가 아닌 부스터 사이클 또는 수량
- `CREATED`, `SUBMITTED`, `PENDING`, `PARTIAL_FILLED`, `UNKNOWN` 상태 주문

코어 보유 자체는 전환 차단 대상이 아니지만, 실제 배포 후 다음 월말 리밸런싱부터 V3.1.1의 고정 $50k 코어 기준을 사용한다.

이 안전게이트가 배포를 막으면 DB를 강제로 초기화하거나 포지션을 임의 삭제하지 않는다. 진행 중 거래가 기존 계약으로 안전하게 종료된 뒤 다시 배포한다.

## 4. V3.1.1 관리계좌 의미

JDSS의 `$50,000`은 Toss 전체 잔고가 아니다. 봇이 위험자산 주문에 사용할 수 있는 **고정 전략원금**이다.

- 코어·부스터 현재 원가가 고정원금을 점유한다.
- 열린 BUY의 미체결 금액과 예상수수료도 예약한다.
- JDSS 실현수익 때문에 실제 현금이 $50,000을 넘어도 초과분은 다시 BUY에 쓰지 않는다.
- JDSS 손실 때문에 가용전략현금이 줄어도 개인 현금으로 자동 보충하지 않는다.
- 개인 USD·QQQ·QQQM은 JDSS 원장에서 제외한다.
- 개인 TQQQ/SOXL은 같은 계좌 혼합 보유를 금지한다.
- SGOV는 OFF이며 유휴 JDSS 원금은 USD 현금이다.

신규 BUY의 최종 상한은 `남은 고정원금`, `JDSS 실제 가용현금`, `Toss 실제 주문가능금액` 중 가장 작은 값이다.

## 5. dry-run 재시작

Oracle dry-run 브로커는 메모리 시뮬레이터이므로 재시작 시 SQLite를 기준으로 복원한다.

1. 코어·부스터 관리수량과 원가를 복원한다.
2. 전체 JDSS 체결원장으로 raw cash를 계산한다.
3. 현재 코어·부스터 원가를 고려해 **$50,000 고정원금 안의 현금만** dry-run buying power로 복원한다.
4. 실현수익 중 고정원금을 넘는 현금은 재사용하지 않는다.
5. 체결 0으로 증명 가능한 DRY 미체결 주문만 동일 broker id로 복원한다.
6. `UNKNOWN`과 재시작 시점 `PARTIAL_FILLED`는 추정복구하지 않고 SAFE_MODE로 확인한다.
7. 새 DRY 주문번호는 과거 완료 주문까지 포함한 최대번호 다음부터 시작한다.

## 6. 배포 후 확인

```bash
sudo systemctl status jd_holdings_bot --no-pager
sudo journalctl -u jd_holdings_bot -n 100 --no-pager
ls -l /home/ubuntu/JD_HOLDINGS/current
grep '^JDSS_TRADING_MODE=' /home/ubuntu/JD_HOLDINGS/shared/.env
```

Telegram에서는 다음을 확인한다.

- `/ping`: 응답 정상
- `/dashboard`: V3.1.1 표시와 고정 $50k 관점
- `/portfolio`: 6개월 코어, 첫 ON 10%, 지속 15%
- `/score`: H40-S3 점수와 게이트
- `/signal`: BUY 2단계 승인
- `/guide`: $50k 고정·$20k H40·$18k S3·SGOV OFF 설명
- `/order`, `/errors`: 미체결·SAFE_MODE 확인
- `/account`: 실제 Toss 전체계좌는 JDSS 원장과 별개임을 확인

`/sgov`는 production 메뉴에 없어야 한다.

조회 전용 smoke test는 TQQQ/SOXL 시세와 인증·시장상태를 확인하며 주문하지 않는다. SGOV 시세 성공은 더 이상 V3.1.1 배포 필수조건이 아니다.

## 7. Runtime verifier

`.github/workflows/verify-oracle-v31-runtime.yml`은 이름은 과거 호환을 위해 V31을 유지하지만 V3.1.1 계약을 검사한다.

- 최신 main SHA와 Oracle current SHA 일치
- V3.1.1 config와 fixed $50k 확인
- forced dry-run 환경변수 확인
- 서비스 active 확인
- 관리원금·승인·fault-injection 테스트
- Toss 조회 smoke test

systemd 재시작 검증은 미국 시장 phase가 `closed`일 때만 수행한다. 장중·프리마켓·애프터마켓이면 서비스 안정성을 우선해 재시작을 생략하고 `PASS_NO_RESTART`로 기록한다.

## 8. 로컬 직접 배포

GitHub Actions를 사용할 수 없을 때만 깨끗하고 원격과 동기화된 `main`에서 실행한다.

```bash
SSH_KEY_PATH=/absolute/path/to/oracle.key \
SERVER_HOST=203.0.113.10 \
SERVER_USER=ubuntu \
SERVER_TARGET_DIR=/home/ubuntu/JD_HOLDINGS \
SYSTEMD_SERVICE=jd_holdings_bot \
REMOTE_PYTHON_BIN=/path/to/python3.12 \
./deploy.sh
```

`SKIP_LOCAL_CHECKS=1`은 같은 커밋이 바로 앞 GitHub Actions에서 검증됐을 때만 사용한다.

## 9. 릴리스·롤백 원칙

과거 GitHub Release와 과거 연구기록은 재현을 위해 보존한다. **버전 전용 ChatOps workflow는 재실행과 운영 혼선을 막기 위해** 해당 릴리스가 끝난 뒤 제거할 수 있다.

릴리스와 Oracle 배포는 별개다. GitHub Release가 있어도 `CURRENT_WORK.md`에 Oracle 배포 성공 SHA가 기록되지 않았다면 서버가 그 버전을 쓰고 있다고 가정하지 않는다.

롤백은 이전 commit 릴리스로 `current` 링크를 되돌린 뒤 `jd_holdings_bot`만 재시작한다. shared DB 스키마 호환성을 먼저 확인하고 다른 프로젝트 프로세스를 일괄 종료하지 않는다.
