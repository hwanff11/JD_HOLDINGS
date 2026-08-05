#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

read_env_value() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return
  fi
  if [[ -f .env ]]; then
    value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' .env)"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
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

: "${SSH_KEY_PATH:?SSH_KEY_PATH가 필요합니다}"
: "${SERVER_HOST:?SERVER_HOST가 필요합니다}"
SERVER_USER="${SERVER_USER:-ubuntu}"
SERVER_TARGET_DIR="${SERVER_TARGET_DIR:-/home/ubuntu/JD_HOLDINGS}"
SYSTEMD_SERVICE="${SYSTEMD_SERVICE:-jd_holdings_bot}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-python3.12}"

if [[ "$SERVER_TARGET_DIR" != /home/*/JD_HOLDINGS && "$SERVER_TARGET_DIR" != /opt/JD_HOLDINGS ]]; then
  echo "안전하지 않은 SERVER_TARGET_DIR입니다: $SERVER_TARGET_DIR" >&2
  exit 1
fi
if [[ "$SYSTEMD_SERVICE" != "jd_holdings_bot" ]]; then
  echo "SYSTEMD_SERVICE는 jd_holdings_bot만 허용합니다." >&2
  exit 1
fi
if [[ ! "$REMOTE_PYTHON_BIN" =~ ^(/[-._/A-Za-z0-9]+|python3\.(11|12|13|14))$ ]]; then
  echo "안전하지 않은 REMOTE_PYTHON_BIN입니다: $REMOTE_PYTHON_BIN" >&2
  exit 1
fi
if [[ ! -f "$SSH_KEY_PATH" ]]; then
  echo "SSH 키 파일이 없습니다: $SSH_KEY_PATH" >&2
  exit 1
fi
if [[ ! -x "$LOCAL_PYTHON" ]]; then
  echo "로컬 가상환경 Python이 없습니다: $LOCAL_PYTHON" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "배포 전 Git 작업트리가 깨끗해야 합니다." >&2
  exit 1
fi
if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "main 브랜치에서만 배포할 수 있습니다." >&2
  exit 1
fi

"$LOCAL_PYTHON" -m pytest
"$LOCAL_PYTHON" -m ruff check .
git push origin main

COMMIT_SHA="$(git rev-parse HEAD)"
ARCHIVE_PATH="$(mktemp "/tmp/jd_holdings_${COMMIT_SHA}.XXXXXX.tar.gz")"
SERVICE_PATH="$(mktemp "/tmp/jd_holdings_service.XXXXXX")"
trap 'rm -f "$ARCHIVE_PATH" "$SERVICE_PATH"' EXIT

git archive --format=tar.gz --output="$ARCHIVE_PATH" HEAD
sed \
  -e "s|__SERVER_USER__|$SERVER_USER|g" \
  -e "s|__TARGET_DIR__|$SERVER_TARGET_DIR|g" \
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
target_dir="$1"
commit_sha="$2"
remote_archive="$3"
remote_service="$4"
service_name="$5"
remote_python="$6"
release_dir="$target_dir/releases/$commit_sha"

if ! command -v "$remote_python" >/dev/null 2>&1; then
  echo "Python 3.11+ 실행파일이 없습니다: $remote_python" >&2
  exit 1
fi
if ! "$remote_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "JD_HOLDINGS는 Python 3.11 이상이 필요합니다." >&2
  exit 1
fi

mkdir -p "$target_dir/releases" "$target_dir/shared/data" "$target_dir/shared/logs"
if [[ ! -f "$target_dir/shared/.env" ]]; then
  echo "서버의 $target_dir/shared/.env를 먼저 생성해야 합니다." >&2
  exit 1
fi
chmod 600 "$target_dir/shared/.env"
mkdir -p "$release_dir"
tar -xzf "$remote_archive" -C "$release_dir"

if [[ ! -x "$target_dir/venv/bin/python" ]]; then
  "$remote_python" -m venv "$target_dir/venv"
fi
"$target_dir/venv/bin/python" -m pip install --upgrade pip
"$target_dir/venv/bin/python" -m pip install "$release_dir"
"$target_dir/venv/bin/jdss" --config "$release_dir/strategy.yaml" validate-config

ln -sfn "$release_dir" "$target_dir/current.new"
mv -Tf "$target_dir/current.new" "$target_dir/current"
sudo install -m 0644 "$remote_service" "/etc/systemd/system/${service_name}.service"
sudo systemctl daemon-reload
sudo systemctl enable "$service_name"
sudo systemctl restart "$service_name"
sudo systemctl is-active --quiet "$service_name"
rm -f "$remote_archive" "$remote_service"
REMOTE

echo "배포 완료: $COMMIT_SHA ($SYSTEMD_SERVICE)"
