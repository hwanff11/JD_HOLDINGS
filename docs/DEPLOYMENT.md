# Oracle 배포 가이드

## 1. 서버 준비

Oracle 인스턴스에 Python 3.11 이상, `python3-venv`, `tar`, systemd가 필요합니다. 기본
배포 경로는 `/home/ubuntu/jd_holdings`이고 기존 CCI 프로젝트와 별도입니다.

서버 공인 IP를 Toss Securities OpenAPI 허용 IP에 등록합니다. 첫 배포 전에 서버에서
다음 파일을 직접 만들고 권한을 제한합니다.

```bash
mkdir -p /home/ubuntu/jd_holdings/shared
install -m 0600 /dev/null /home/ubuntu/jd_holdings/shared/.env
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
SERVER_TARGET_DIR=/home/ubuntu/jd_holdings
SYSTEMD_SERVICE=jd_holdings_bot
```

Git 작업트리가 깨끗한 `main`이어야 하며 remote URL에는 토큰을 포함하지 않습니다.

```bash
git remote -v
./deploy.sh
```

배포는 테스트와 린트 → GitHub push → commit별 릴리스 업로드 → 의존성 설치 → 설정
검증 → `current` 심볼릭 링크 교체 → 전용 systemd 서비스 재시작 순서로 수행됩니다.
DB, `.env`, 로그는 `shared`에 남아 새 릴리스와 분리됩니다.

## 3. 검증

```bash
sudo systemctl status jd_holdings_bot --no-pager
sudo journalctl -u jd_holdings_bot -n 100 --no-pager
ls -l /home/ubuntu/jd_holdings/current
```

Telegram `/ping`, `/dashboard`, `/score TQQQ`를 확인합니다. 실주문 전에는 로컬 또는
서버에서 `jdss toss-smoke`를 실행해 조회 전용 인증을 확인합니다.

## 4. 롤백

이전 commit 릴리스로 `current` 링크를 되돌린 뒤 전용 서비스만 재시작합니다.

```bash
ln -sfn /home/ubuntu/jd_holdings/releases/<previous-commit> \
  /home/ubuntu/jd_holdings/current.new
mv -Tf /home/ubuntu/jd_holdings/current.new /home/ubuntu/jd_holdings/current
sudo systemctl restart jd_holdings_bot
```

롤백 시에도 `shared` DB의 스키마 호환성을 먼저 확인합니다. 기존 CCI 서비스나 다른
Python 프로세스를 일괄 종료하지 않습니다.
