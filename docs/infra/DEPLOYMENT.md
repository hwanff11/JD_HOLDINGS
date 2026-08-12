# Oracle 배포 가이드

Oracle 배포 대상은 검증된 최신 `main`의 **JDSS V3.1 dry-run 계약**이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`를 따르며, 실제 Oracle 배포 SHA와 현재 서버 버전은 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)만 기준으로 확인한다. V3.1도 설정과 애플리케이션 코드가 live 시작을 모두 거부한다.

배포 smoke test는 TQQQ·SOXL·SGOV 시세와 미국장 캘린더를 조회할 뿐 주문하지 않는다.

## 1. 서버 준비

Oracle 인스턴스에 Python 3.11 이상, `python3-venv`, `tar`, systemd가 필요하다. 기본 배포 경로는 `/home/ubuntu/JD_HOLDINGS`다. 서버 Python이 3.11 미만이면 Python 3.12와 해당 `venv`를 먼저 설치하고 실제 실행파일을 배포 설정에 지정한다.

DB는 `/home/ubuntu/JD_HOLDINGS/shared/data/jdss.db`를 계속 사용한다. 기존 스키마에 필요한 테이블·컬럼은 애플리케이션 시작 시 비파괴적으로 보강한다.

서버 공인 IP를 Toss Securities OpenAPI 허용 IP에 등록하고 첫 배포 전에 다음 파일을 만든다.

```bash
mkdir -p /home/ubuntu/JD_HOLDINGS/shared
install -m 0600 /dev/null /home/ubuntu/JD_HOLDINGS/shared/.env
```

`.env.example`을 기준으로 Telegram Chat ID, Toss 앱 키/시크릿 및 계좌 순번을 입력한다. live 승격 전까지 다음 값은 바꾸지 않는다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

## 2. 배포 경로

권장 경로는 GitHub Actions `Deploy Oracle Dry Run` 한 가지다. GitHub Environment `oracle-dry-run`에 `ORACLE_SSH_KEY`, `ORACLE_HOST` secret과 필요한 variable을 보관한다. 비밀값은 저장소 파일이나 대화에 복사하지 않는다.

워크플로는 최신 `main`을 체크아웃하고 원격 `main`과 일치하는지 검증한 뒤 Ruff·pytest·설정·버전을 확인한다. 성공한 커밋만 commit별 릴리스 경로에 배치하고 서버 `.env`를 다시 `dry_run`으로 강제한다.

### 전략 버전 변경 사전점검

`config_version`이 기존 서버와 달라지는 배포(V3.0→V3.1 등)는 기존 사이클에 새 전략의 자금 한도나 단계 규칙이 중간 적용되는 것을 금지한다.

배포 스크립트는 새 코드를 검증한 뒤 기존 서비스를 정지하고 DB의 최종 상태를 다시 확인한다. 다음 중 하나라도 있으면 버전 변경을 중단하고 기존 서비스를 다시 시작한다.

- `positions`에 `EMPTY`가 아닌 부스터 사이클 또는 수량이 남아 있음
- `CREATED`, `SUBMITTED`, `PENDING`, `PARTIAL_FILLED`, `UNKNOWN` 상태의 미완료 주문이 존재함

코어 보유수량 자체는 이 사전점검의 차단 대상이 아니다. V3.1 코어는 다음 완료 월말 신호부터 6개월 추세와 10→15% 규칙으로 이어간다. 진행 중인 부스터와 미체결 주문은 기존 전략 계약으로 안전하게 끝낸 뒤 버전 변경 배포를 다시 실행한다.

ChatGPT 연결에서 수동 Actions dispatch가 제공되지 않으면 저장소 소유자가 제목을 `[deploy-oracle-dry-run]`으로 시작하는 이슈를 생성하는 ChatOps 경로를 사용한다. Actions는 실행 시점의 최신 `main`을 다시 검증하므로 오래된 PR SHA를 임의로 배포하지 않는다.

로컬 직접 배포가 필요한 경우에만 깨끗하고 원격과 동기화된 `main`에서 실행한다.

```dotenv
SSH_KEY_PATH=/absolute/path/to/oracle.key
SERVER_HOST=203.0.113.10
SERVER_USER=ubuntu
SERVER_TARGET_DIR=/home/ubuntu/JD_HOLDINGS
SYSTEMD_SERVICE=jd_holdings_bot
REMOTE_PYTHON_BIN=/path/to/python3.12
```

```bash
git remote -v
env -u GITHUB_TOKEN ./deploy.sh
```

`SKIP_LOCAL_CHECKS=1`은 같은 커밋을 바로 앞 GitHub Actions 단계에서 검증한 경우에만 사용한다.

## 3. 운영 데이터와 서비스

DB, `.env`, 로그와 yfinance 캐시는 `shared`에 남고 commit별 코드 릴리스와 분리한다.

- DB: `/home/ubuntu/JD_HOLDINGS/shared/data/jdss.db`
- 로그: `/home/ubuntu/JD_HOLDINGS/shared/logs/jdss.log`
- 캐시: `/home/ubuntu/JD_HOLDINGS/shared/data/cache`
- 비밀정보: `/home/ubuntu/JD_HOLDINGS/shared/.env`
- 현재 코드: `/home/ubuntu/JD_HOLDINGS/current`

systemd 서비스는 `JDSS_CACHE_PATH`를 shared 캐시로, `JDSS_CONFIG_PATH`를 `/home/ubuntu/JD_HOLDINGS/current/strategy.yaml`로 지정한다. 서비스는 `UMask=0077` 등 운영 사용자 전용 권한과 기존 보안 hardening을 유지하며 세부 기준은 [`SECURITY.md`](SECURITY.md)를 따른다.

### JDSS managed account

V3.1의 `$20,000`은 전체 Toss 계좌잔고가 아니라 JDSS의 초기 관리배정금이다. 애플리케이션은 전체 JDSS 주문이력의 누적 체결로 managed cash를 재구성하고, JDSS 코어·부스터·관리 SGOV만 managed equity에 포함한다.

- 같은 계좌에 개인 USD 현금이 더 있어도 JDSS BUY 한도에는 사용하지 않는다.
- 개인 SGOV는 비관리 수량으로 남기며 자동 매도하지 않는다.
- **개인 TQQQ 또는 SOXL을 같은 계좌에 함께 보유하는 운영은 지원하지 않는다.** Toss 잔고가 같은 티커를 합산하므로 JDSS 코어+부스터 수량과 분리할 수 없고 Reconciliation이 SAFE_MODE로 전환한다.
- 미체결 BUY 잔여금은 예상 수수료까지 포함해 managed cash에서 예약한다.
- 두 실행 콜백이 동시에 들어와도 managed cash 확인과 주문예약은 하나의 SQLite `BEGIN IMMEDIATE` 트랜잭션에서 수행한다.

### dry-run cold restart

Oracle dry-run 브로커는 외부 주문 서버가 없는 메모리 시뮬레이터다. 프로세스가 재시작되면 SQLite 원장을 기준으로 다음 순서로 복원한다.

1. 코어·부스터·JDSS 관리 SGOV 수량과 원가를 메모리 보유량으로 재구성한다.
2. 초기 `$20,000`에서 시작해 전체 JDSS 주문의 실제 누적 체결과 설정 매수·매도 수수료를 반영하여 managed cash를 재구성한다. 닫힌 과거 사이클의 실현손익도 재시작 뒤 이어진다.
3. 체결 0으로 증명 가능한 DRY 미체결 주문만 같은 broker order id로 자동 복원한다.
4. 새 DRY 주문번호는 열린 주문뿐 아니라 완료 주문까지 포함한 전체 역사에서 가장 큰 `DRY-########` 번호 다음부터 이어간다.
5. `UNKNOWN` 또는 재시작 시점의 `PARTIAL_FILLED` 주문은 실제 메모리 브로커 상태를 추정하지 않는다. 해당 주문은 자동복원하지 않고 Reconciliation에서 SAFE_MODE로 확인한다.
6. DB에 열린 주문이 있는데 broker order id가 없거나 복원된 브로커 주문과 DB가 다르면 신규매수를 차단한다.

PENDING 지정가는 이후 주문 모니터 조회 때 최신 dry-run 현재가로 체결 가능 여부를 다시 평가한다. 프로세스 내 누적 부분체결은 이전 누적값과의 신규 delta만 보유량·현금에 반영한다. 복원할 수 없는 주문을 주문 모니터가 다시 만난 경우에도 예외로 반복 종료하지 않고 SAFE_MODE 이벤트를 유지한다.

managed cash가 재시작 후 유지되더라도 Oracle dry-run 잔고를 정식 투자성과 자료로 사용하지 않는다. 실제 호가·환전·세금·시장충격을 완전히 재현하지 않는 시뮬레이션이므로 전략 성과 평가는 `JDSS V3 Backtest` 결과를 기준으로 한다.

## 4. 배포 후 검증

```bash
sudo systemctl status jd_holdings_bot --no-pager
sudo journalctl -u jd_holdings_bot -n 100 --no-pager
ls -l /home/ubuntu/JD_HOLDINGS/current
grep '^JDSS_TRADING_MODE=' /home/ubuntu/JD_HOLDINGS/shared/.env
```

Telegram에서는 `/ping`, `/portfolio`, `/dashboard`, `/account`, `/sgov`, `/bt`, `/guide`를 확인한다.

V3.1에서 특히 확인할 내용은 다음과 같다.

- `/portfolio`: 6개월 월간 추세, 첫 ON 10%→지속 15% 코어와 부스터 분리수량
- 코어 승인: 현재가가 신호가격보다 내려가도 신호 당시 계획주수보다 주문수량이 늘어나지 않는지
- `/status`: 부스터 평단·TP1 +4% 약 30%·TP2 +10%가 코어 보유수량과 섞이지 않는지
- `/guide`: H40 자금 상한 40%와 S3 정상 최대 신규투입 36%가 구분되는지
- `/sgov`: JDSS 관리 SGOV와 비관리 SGOV가 분리되고 SAFE_MODE가 없는지
- managed cash가 초기 배정금+누적체결손익·수수료 기준으로 재시작 전후 이어지는지
- SGOV 현금화 후 최종 승인 자동 재개, 활성 의도 중 재예치 차단, 60초 미체결 재가격
- 재시작 전후 증명 가능한 DRY 미체결 주문과 Reconciliation 일치
- `UNKNOWN`/부분체결 재시작 또는 누락 broker id가 있으면 SAFE_MODE에서 신규매수가 차단되는지
- `/bt`: V3.1 통합 포트폴리오 결과와 코어·부스터 체결수 표시
- `jdss toss-smoke`: TQQQ·SOXL·SGOV 시세와 조회 전용 인증 성공

배포 후 Reconciliation은 브로커 TQQQ·SOXL 수량과 `코어 수량 + 부스터 수량`, 미체결 주문, SGOV 관리 원장을 비교한다. 불일치나 SAFE_MODE가 있으면 코어·부스터 신규매수를 진행하지 않고 원인을 먼저 해결한다.

`JDSS_TRADING_MODE=live`는 설정하지 않는다. V3.1 live 승격은 별도 승인과 코드 변경 없이는 허용하지 않는다.

## 5. 릴리스 이력과 다음 릴리스

기존 `v3.0.0` GitHub Release는 과거 운영 기준으로 보존한다. 해당 릴리스에 사용한 **버전 전용 ChatOps workflow는 재실행과 운영 혼선을 막기 위해** 릴리스 완료 후 제거했다.

V3.1을 정식 GitHub Release로 만들 경우 패키지·설정 버전, 전략 식별자, `live_enabled: false`, 최신 `main`의 CI·Security·Dry Run·백테스트 결과를 확인한 뒤 별도 릴리스 절차를 실행한다.

릴리스 여부와 Oracle 배포 여부는 같은 의미가 아니다. `main`에 V3.1이 병합돼도 `CURRENT_WORK.md`에 V3.1 Oracle 배포 성공이 기록되기 전까지 서버는 기존 배포 버전을 사용한다고 판단한다.

## 6. 롤백

이전 commit 릴리스로 `current` 링크를 되돌린 뒤 전용 서비스만 재시작한다.

```bash
ln -sfn /home/ubuntu/JD_HOLDINGS/releases/<previous-commit> \
  /home/ubuntu/JD_HOLDINGS/current.new
mv -Tf /home/ubuntu/JD_HOLDINGS/current.new /home/ubuntu/JD_HOLDINGS/current
sudo systemctl restart jd_holdings_bot
```

롤백 전에는 shared DB 스키마가 이전 코드와 호환되는지 확인한다. 다른 프로젝트나 Python 프로세스를 일괄 종료하지 않는다.