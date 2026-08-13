#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

read_env_value() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then printf '%s' "$value"; return; fi
  if [[ -f .env ]]; then
    value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' .env)"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    printf '%s' "$value"
  fi
}

SSH_KEY_PATH="$(read_env_value SSH_KEY_PATH)"
SERVER_HOST="$(read_env_value SERVER_HOST)"
SERVER_USER="$(read_env_value SERVER_USER)"
SERVER_TARGET_DIR="$(read_env_value SERVER_TARGET_DIR)"
SYSTEMD_SERVICE="$(read_env_value SYSTEMD_SERVICE)"
REMOTE_PYTHON_BIN="$(read_env_value REMOTE_PYTHON_BIN)"
LOCAL_PYTHON="${LOCAL_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
SKIP_LOCAL_CHECKS="${SKIP_LOCAL_CHECKS:-0}"

: "${SSH_KEY_PATH:?SSH_KEY_PATH가 필요합니다}"
: "${SERVER_HOST:?SERVER_HOST가 필요합니다}"
SERVER_USER="${SERVER_USER:-ubuntu}"
SERVER_TARGET_DIR="${SERVER_TARGET_DIR:-/home/ubuntu/JD_HOLDINGS}"
SYSTEMD_SERVICE="${SYSTEMD_SERVICE:-jd_holdings_bot}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-python3.12}"

if [[ "$SERVER_TARGET_DIR" != /home/*/JD_HOLDINGS && "$SERVER_TARGET_DIR" != /opt/JD_HOLDINGS ]]; then
  echo "안전하지 않은 SERVER_TARGET_DIR입니다: $SERVER_TARGET_DIR" >&2; exit 1
fi
if [[ "$SYSTEMD_SERVICE" != "jd_holdings_bot" ]]; then
  echo "SYSTEMD_SERVICE는 jd_holdings_bot만 허용합니다." >&2; exit 1
fi
if [[ ! -f "$SSH_KEY_PATH" ]]; then echo "SSH 키 파일이 없습니다." >&2; exit 1; fi
if [[ "$SKIP_LOCAL_CHECKS" != "0" && "$SKIP_LOCAL_CHECKS" != "1" ]]; then exit 1; fi
if [[ -n "$(git status --porcelain)" ]]; then echo "배포 전 Git 작업트리가 깨끗해야 합니다." >&2; exit 1; fi
if [[ "$(git branch --show-current)" != "main" ]]; then echo "main 브랜치에서만 배포할 수 있습니다." >&2; exit 1; fi

git fetch origin main --no-tags
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "로컬 main이 origin/main과 정확히 일치해야 합니다." >&2; exit 1
fi
if [[ "$SKIP_LOCAL_CHECKS" == "0" ]]; then
  "$LOCAL_PYTHON" -m pytest
  "$LOCAL_PYTHON" -m ruff check .
  "$LOCAL_PYTHON" -m jd_holdings.cli --config strategy.yaml validate-config
fi

grep -q 'JDSS-3.2.2-RS6M-ONEWAY-HWM75' strategy.yaml
COMMIT_SHA="$(git rev-parse HEAD)"
ARCHIVE_PATH="$(mktemp "/tmp/jd_holdings_${COMMIT_SHA}.XXXXXX.tar.gz")"
SERVICE_PATH="$(mktemp "/tmp/jd_holdings_service.XXXXXX")"
trap 'rm -f "$ARCHIVE_PATH" "$SERVICE_PATH"' EXIT
git archive --format=tar.gz --output="$ARCHIVE_PATH" HEAD
sed -e "s|__SERVER_USER__|$SERVER_USER|g" -e "s|__TARGET_DIR__|$SERVER_TARGET_DIR|g" \
  systemd/jd_holdings_bot.service.template > "$SERVICE_PATH"
SSH_ARGS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -i "$SSH_KEY_PATH")
REMOTE_ARCHIVE="/tmp/jd_holdings_${COMMIT_SHA}.tar.gz"
REMOTE_SERVICE="/tmp/${SYSTEMD_SERVICE}.service"
scp "${SSH_ARGS[@]}" "$ARCHIVE_PATH" "$SERVER_USER@$SERVER_HOST:$REMOTE_ARCHIVE"
scp "${SSH_ARGS[@]}" "$SERVICE_PATH" "$SERVER_USER@$SERVER_HOST:$REMOTE_SERVICE"

ssh "${SSH_ARGS[@]}" "$SERVER_USER@$SERVER_HOST" bash -s -- \
  "$SERVER_TARGET_DIR" "$COMMIT_SHA" "$REMOTE_ARCHIVE" "$REMOTE_SERVICE" \
  "$SYSTEMD_SERVICE" "$REMOTE_PYTHON_BIN" <<'REMOTE'
set -Eeuo pipefail
target_dir="$1"; commit_sha="$2"; remote_archive="$3"; remote_service="$4"; service_name="$5"; remote_python="$6"
release_dir="$target_dir/releases/$commit_sha"
command -v "$remote_python" >/dev/null
"$remote_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'
mkdir -p "$target_dir/releases" "$target_dir/shared/data/cache" "$target_dir/shared/logs"
env_file="$target_dir/shared/.env"
if [[ ! -f "$env_file" ]]; then echo "서버의 $target_dir/shared/.env를 먼저 생성해야 합니다." >&2; exit 1; fi
sed -i '/^JDSS_TRADING_MODE=/d;/^JDSS_LIVE_CONFIRMATION=/d' "$env_file"
printf '\nJDSS_TRADING_MODE=dry_run\nJDSS_LIVE_CONFIRMATION=\n' >> "$env_file"
chmod 600 "$env_file"
mkdir -p "$release_dir"
tar -xzf "$remote_archive" -C "$release_dir"
if [[ ! -x "$target_dir/venv/bin/python" ]]; then
  "$remote_python" -m venv "$target_dir/venv"
fi
"$target_dir/venv/bin/python" -m pip install --upgrade pip
"$target_dir/venv/bin/python" -m pip install "$release_dir"
"$target_dir/venv/bin/jdss" --config "$release_dir/strategy.yaml" validate-config

# Freeze the old process before changing strategy state.
sudo systemctl stop "$service_name" || true
if [[ -f "$target_dir/current/strategy.yaml" && -f "$target_dir/shared/data/jdss.db" ]]; then
  if ! "$remote_python" - \
    "$target_dir/current/strategy.yaml" "$release_dir/strategy.yaml" "$target_dir/shared/data/jdss.db" <<'PY'
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

old_path, new_path, db_path = map(Path, sys.argv[1:])
pattern = re.compile(r'^config_version:\s*["\']?([^"\'\s]+)', re.MULTILINE)
def version(path: Path) -> str:
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"config_version을 읽을 수 없습니다: {path}")
    return match.group(1)

old_version = version(old_path)
new_version = version(new_path)
if old_version == new_version:
    raise SystemExit(0)

connection = sqlite3.connect(db_path)
connection.row_factory = sqlite3.Row
active_positions = connection.execute(
    "SELECT symbol, state, qty FROM positions WHERE state <> 'EMPTY' OR qty <> 0"
).fetchall()
open_orders = connection.execute(
    "SELECT symbol, purpose, status FROM orders WHERE status IN ('CREATED', 'SUBMITTED', 'PENDING', 'PARTIAL_FILLED', 'UNKNOWN')"
).fetchall()
connection.close()

# V3.1.1 -> V3.2.2 changes the ledger model. Deployment is already forced to
# dry_run above, so preserve the entire old simulation DB and start a clean one.
if new_version == "3.2.2":
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db_path.with_name(f"jdss.v322-migration.{old_version}.{stamp}.db")
    shutil.copy2(db_path, backup)
    db_path.unlink()
    print(
        f"V3.2.2 dry-run 원장 전환: {old_version} -> {new_version}; "
        f"backup={backup}; legacy_positions={len(active_positions)}; open_orders={len(open_orders)}"
    )
    raise SystemExit(0)

if active_positions or open_orders:
    raise SystemExit(42)
print(f"전략 버전 변경 사전점검 통과: {old_version} -> {new_version}")
PY
  then
    sudo systemctl start "$service_name" || true
    echo "전략 버전 변경 사전점검 실패로 기존 서비스를 복구했습니다." >&2
    exit 1
  fi
fi

ln -sfn "$release_dir" "$target_dir/current.new"
mv -Tf "$target_dir/current.new" "$target_dir/current"
sudo install -m 0644 "$remote_service" "/etc/systemd/system/${service_name}.service"
sudo systemctl daemon-reload
if ! sudo systemctl is-enabled --quiet "$service_name"; then sudo systemctl enable "$service_name"; fi
sudo systemctl restart "$service_name"
sudo systemctl is-active --quiet "$service_name"
set -a; source "$env_file"; set +a
test "${JDSS_TRADING_MODE:-}" = "dry_run"
test -z "${JDSS_LIVE_CONFIRMATION:-}"
"$target_dir/venv/bin/jdss" --config "$target_dir/current/strategy.yaml" init-db
"$target_dir/venv/bin/jdss" --config "$target_dir/current/strategy.yaml" validate-config
"$target_dir/venv/bin/jdss" --config "$target_dir/current/strategy.yaml" toss-smoke
sudo systemctl is-active --quiet "$service_name"
rm -f "$remote_archive" "$remote_service"
REMOTE

echo "배포 완료: $COMMIT_SHA ($SYSTEMD_SERVICE, V3.2.2 forced dry_run, smoke OK)"
