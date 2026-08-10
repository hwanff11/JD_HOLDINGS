# Oracle 배포 가이드

현재 Oracle 운영본은 **JDSS-2.2.2-SGOV**이다. `dry_run`으로 배포됐으며 SGOV를 포함한 실주문 잠금은 해제하지 않는다.

현재 운영 스냅샷은 서비스 active, 패키지·설정 `2.2.2`, 거래 모드 `dry_run`, 빈 `JDSS_LIVE_CONFIRMATION`이다. TQQQ·SOXL·SGOV 시세와 미국장 캘린더 조회 smoke test를 통과했다. 운영 SHA는 문서에 반복 기록하지 않고 `/home/ubuntu/JD_HOLDINGS/current` 링크와 배포 출력을 실시간 기준으로 사용한다.

## 1. 서버 준비

Oracle 인스턴스에 Python 3.11 이상, `python3-venv`, `tar`, systemd가 필요합니다. 기본
배포 경로는 `/home/ubuntu/JD_HOLDINGS`이고 기존 CCI 프로젝트와 별도입니다.

과거 확인된 기존 서버 기본 Python은 3.8.10이므로 그대로는 배포할 수 없습니다.
Python 3.12와 해당 버전의 `venv` 모듈을 먼저 설치하고, 로컬 배포 설정에 실제 실행파일을
지정합니다.

서버 공인 IP를 Toss Securities OpenAPI 허용 IP에 등록합니다. 첫 배포 전에 서버에서
다음 파일을 직접 만들고 권한을 제한합니다.

```bash
mkdir -p /home/ubuntu/JD_HOLDINGS/shared
install -m 0600 /dev/null /home/ubuntu/JD_HOLDINGS/shared/.env
```

`.env.example`을 기준으로 Telegram 봇 토큰, 개인 Chat ID 하나, Toss 앱 키/시크릿 및
계좌 순번을 입력합니다. 기준선 검증이 끝날 때까지 아래 값은 유지합니다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

## 2. 로컬 배포 설정

로컬 `.env`의 배포 항목에 SSH 키와 서버를 설정합니다.

```dotenv
SSH_KEY_PATH=/absolute/path/to/oracle.key
SERVER_HOST=203.0.113.10
SERVER_USER=ubuntu
SERVER_TARGET_DIR=/home/ubuntu/JD_HOLDINGS
SYSTEMD_SERVICE=jd_holdings_bot
REMOTE_PYTHON_BIN=/home/ubuntu/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12
```

Git 작업트리가 깨끗한 `main`이어야 하며, 로컬 HEAD가 `origin/main`과 정확히 일치해야 합니다. remote URL에는 토큰을 포함하지 않습니다.

```bash
git remote -v
env -u GITHUB_TOKEN ./deploy.sh
```

권장 경로는 GitHub Actions의 수동 워크플로 `Deploy Oracle Dry Run`이다. GitHub Environment `oracle-dry-run`에 `ORACLE_SSH_KEY`, `ORACLE_HOST` secret과 필요 시 `ORACLE_USER`, `ORACLE_TARGET_DIR`, `ORACLE_PYTHON` variable을 설정한다. 워크플로는 배포 커밋이 `main` 계보인지 확인하고 Ruff·pytest·설정·버전을 한 번 검증한 뒤, `deploy.sh`에 이미 검증했음을 알려 중복 실행만 생략한다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

워크플로 파일: `.github/workflows/deploy-oracle-dry-run.yml`

### ChatGPT에서 배포 요청

ChatGPT의 GitHub 연결에서 수동 Actions dispatch가 직접 제공되지 않는 환경은 GitHub 이슈를 배포 요청으로 사용한다. ChatGPT는 다음을 순서대로 수행한다.

1. 대상 PR의 필수 CI와 JDSS Dry Run이 성공했는지 확인한다.
2. PR을 `main`에 병합하고 최신 40자리 `main` SHA를 확인한다.
3. 저장소 소유자 계정으로 제목이 `[deploy-oracle-dry-run] JDSS <SHA>`인 이슈를 생성한다.
4. 이슈 본문에 아래 형식을 정확히 넣는다.

```text
ref: 0123456789abcdef0123456789abcdef01234567
```

5. Actions는 이슈 작성자가 저장소 소유자인지, SHA가 40자리인지, SHA가 `main` 계보인지 검증한다.
6. 검증 후 pytest·Ruff·설정·버전 게이트와 Oracle dry-run 배포·Toss smoke test를 실행하고, 성공 또는 실패 결과를 같은 이슈에 댓글로 남긴다.

ChatGPT는 서버 SSH 키나 Toss 비밀값을 직접 취급하지 않는다. 비밀값은 GitHub Environment `oracle-dry-run`과 Oracle shared `.env`에만 유지한다. 이 ChatOps 경로도 `dry_run`만 허용하며 live 전환에는 사용하지 않는다.

로컬 직접 배포는 기본으로 pytest·Ruff·설정 검증을 실행한다. `SKIP_LOCAL_CHECKS=1`은 같은 커밋을 바로 앞 단계에서 검증한 GitHub Actions만 사용한다.

배포는 원격 `main` 일치 확인 → 필요 시 로컬 검증 → commit별 릴리스 업로드 → 서버 `dry_run` 잠금 → 의존성·설정 검증 → `current` 링크 교체 → systemd 재시작 1회 → Toss 조회 전용 smoke test 순으로 한 번에 수행된다. pip 자체 업그레이드와 systemd enable은 최초 구성 시에만 실행한다.
DB, `.env`, 로그는 `shared`에 남아 새 릴리스와 분리됩니다.
Telegram 백테스트의 yfinance 캐시도
`/home/ubuntu/JD_HOLDINGS/shared/data/cache`에 저장되어 읽기 전용 릴리스와 분리됩니다.
systemd 서비스는 `JDSS_CACHE_PATH`를 shared 캐시로, `JDSS_CONFIG_PATH`를
`/home/ubuntu/JD_HOLDINGS/current/strategy.yaml`로 지정해
설치형 패키지에서도 현재 릴리스의 설정을 사용합니다.

운영 데이터 경로는 다음과 같다.

- DB: `/home/ubuntu/JD_HOLDINGS/shared/data/jdss.db`
- 로그: `/home/ubuntu/JD_HOLDINGS/shared/logs/jdss.log`
- 캐시: `/home/ubuntu/JD_HOLDINGS/shared/data/cache`
- 비밀정보: `/home/ubuntu/JD_HOLDINGS/shared/.env`

## 3. 검증

```bash
sudo systemctl status jd_holdings_bot --no-pager
sudo journalctl -u jd_holdings_bot -n 100 --no-pager
ls -l /home/ubuntu/JD_HOLDINGS/current
grep '^JDSS_TRADING_MODE=' /home/ubuntu/JD_HOLDINGS/shared/.env
```

Telegram `/ping`, `/dashboard`, `/account`, `/sgov`, `/backtest`를 확인합니다. `/sgov`는 JDSS 관리 SGOV와 비관리 SGOV를 구분하고 SAFE_MODE가 없어야 합니다. 2.2.2에서는 현금화 의도 DB 마이그레이션, SGOV 현금화 후 최종 승인 자동 재개, 활성 의도 중 재예치 차단, 60초 미체결 재가격을 추가로 확인합니다. 인자 없는
`/backtest`는 SOXL 최근 300거래일을 실행하며, 신호·매수·미체결·TP1·TP2 내역이
종목당 최근 15건까지 표시되는지 확인합니다. `/account`는 미국주식과
수수료 반영 평가손익만 표시해야 합니다. 실주문 전에는 서버에서
`jdss toss-smoke`를 실행해 TQQQ·SOXL·SGOV 시세와 조회 전용 인증을 확인합니다.

배포 후에는 실제 브로커 잔고·미체결 주문·SQLite 포지션과 주문 상태 및 SGOV 관리 원장을 Reconciliation한다. 2.1 DB에서 처음 생성되는 SGOV 관리 수량은 0이며 기존 계좌 SGOV를 자동 인수하지 않는다. 불일치가 있거나 `SAFE_MODE`가 활성화되면 신규매수를 중지한 채 원인을 먼저 해결한다. `dry_run` 관찰과 운영자 확인이 끝나기 전에는 `JDSS_TRADING_MODE=live` 또는 실주문 확인 문자열을 설정하지 않는다.

## 4. 롤백

이전 commit 릴리스로 `current` 링크를 되돌린 뒤 전용 서비스만 재시작합니다.

```bash
ln -sfn /home/ubuntu/JD_HOLDINGS/releases/<previous-commit> \
  /home/ubuntu/JD_HOLDINGS/current.new
mv -Tf /home/ubuntu/JD_HOLDINGS/current.new /home/ubuntu/JD_HOLDINGS/current
sudo systemctl restart jd_holdings_bot
```

롤백 시에도 `shared` DB의 스키마 호환성을 먼저 확인합니다. 기존 CCI 서비스나 다른
Python 프로세스를 일괄 종료하지 않습니다.
