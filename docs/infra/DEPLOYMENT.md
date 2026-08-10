# Oracle 배포 가이드

현재 배포 대상은 `main`의 **JDSS-2.1.0-FINAL**이다. 최초 반영은 반드시 `dry_run`으로 수행하며, 이 단계에서는 실주문 잠금을 해제하지 않는다.

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
./deploy.sh
```

권장 경로는 GitHub Actions의 수동 워크플로 `Deploy Oracle Dry Run`이다. GitHub Environment `oracle-dry-run`에 `ORACLE_SSH_KEY`, `ORACLE_HOST` secret과 필요 시 `ORACLE_USER`, `ORACLE_TARGET_DIR`, `ORACLE_PYTHON` variable을 설정한다. 워크플로는 배포 커밋이 `main` 계보인지 확인하고, Ruff·pytest·설정 검증·FINAL 버전 검사를 다시 통과한 뒤 서버 `.env`를 아래처럼 강제 잠근다.

```dotenv
JDSS_TRADING_MODE=dry_run
JDSS_LIVE_CONFIRMATION=
```

워크플로 파일: `.github/workflows/deploy-oracle-dry-run.yml`

배포는 원격 `main` 일치 확인 → 테스트와 린트 → commit별 릴리스 업로드 → 의존성 설치 → 설정
검증 → `current` 심볼릭 링크 교체 → 전용 systemd 서비스 재시작 순서로 수행됩니다.
DB, `.env`, 로그는 `shared`에 남아 새 릴리스와 분리됩니다.
Telegram 백테스트의 yfinance 캐시도
`/home/ubuntu/JD_HOLDINGS/shared/data/cache`에 저장되어 읽기 전용 릴리스와 분리됩니다.
systemd 서비스는 `JDSS_CACHE_PATH`를 shared 캐시로, `JDSS_CONFIG_PATH`를
`/home/ubuntu/JD_HOLDINGS/current/strategy.yaml`로 지정해
설치형 패키지에서도 현재 릴리스의 설정을 사용합니다.

## 3. 검증

```bash
sudo systemctl status jd_holdings_bot --no-pager
sudo journalctl -u jd_holdings_bot -n 100 --no-pager
ls -l /home/ubuntu/JD_HOLDINGS/current
grep '^JDSS_TRADING_MODE=' /home/ubuntu/JD_HOLDINGS/shared/.env
```

Telegram `/ping`, `/dashboard`, `/account`, `/backtest`를 확인합니다. 인자 없는
`/backtest`는 SOXL 최근 300거래일을 실행하며, 신호·매수·미체결·TP1·TP2 내역이
종목당 최근 15건까지 표시되는지 확인합니다. `/account`는 미국주식과
수수료 반영 평가손익만 표시해야 합니다. 실주문 전에는 서버에서
`jdss toss-smoke`를 실행해 조회 전용 인증을 확인합니다.

배포 후에는 실제 브로커 잔고·미체결 주문·SQLite 포지션과 주문 상태를 Reconciliation한다. 불일치가 있거나 `SAFE_MODE`가 활성화되면 신규매수를 중지한 채 원인을 먼저 해결한다. `dry_run` 관찰과 운영자 확인이 끝나기 전에는 `JDSS_TRADING_MODE=live` 또는 실주문 확인 문자열을 설정하지 않는다.

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
