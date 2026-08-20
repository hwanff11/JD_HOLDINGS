#!/usr/bin/env bash
set -Eeuo pipefail

OLD_DIR="${OLD_DIR:-/home/ubuntu/JD_HOLDINGS}"
NEW_DIR="${NEW_DIR:-/home/ubuntu/JH_HOLDINGS}"
OLD_SERVICE="${OLD_SERVICE:-jd_holdings_bot}"
NEW_SERVICE="${NEW_SERVICE:-jh_holdings_bot}"
REMOTE_PYTHON="${REMOTE_PYTHON:-}"
REPO_URL="${REPO_URL:-https://github.com/hwanff11/JH_HOLDINGS.git}"
MIGRATION_REF="${MIGRATION_REF:-main}"
SERVICE_TEMPLATE="${SERVICE_TEMPLATE:?SERVICE_TEMPLATE is required}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="${OLD_DIR}.migration-backup-${STAMP}"

fail_preflight() { echo "Migration preflight failed: $*" >&2; exit 1; }
[[ "$OLD_DIR" == /home/*/JD_HOLDINGS || "$OLD_DIR" == /opt/JD_HOLDINGS ]] || fail_preflight "unsafe OLD_DIR=$OLD_DIR"
[[ "$NEW_DIR" == /home/*/JH_HOLDINGS || "$NEW_DIR" == /opt/JH_HOLDINGS ]] || fail_preflight "unsafe NEW_DIR=$NEW_DIR"
[[ "$OLD_DIR" != "$NEW_DIR" ]] || fail_preflight "OLD_DIR and NEW_DIR are identical"
[[ "$OLD_SERVICE" == jd_holdings_bot ]] || fail_preflight "unexpected OLD_SERVICE=$OLD_SERVICE"
[[ "$NEW_SERVICE" == jh_holdings_bot ]] || fail_preflight "unexpected NEW_SERVICE=$NEW_SERVICE"
[[ -d "$OLD_DIR" ]] || fail_preflight "legacy directory missing: $OLD_DIR"
[[ -f "$OLD_DIR/shared/.env" ]] || fail_preflight "legacy env missing: $OLD_DIR/shared/.env"
[[ -f "$OLD_DIR/shared/data/jdss.db" ]] || fail_preflight "legacy DB missing: $OLD_DIR/shared/data/jdss.db"
command -v git >/dev/null || fail_preflight "git missing"
sudo systemctl is-active --quiet "$OLD_SERVICE" || fail_preflight "legacy service is not active: $OLD_SERVICE"

if [[ -n "$REMOTE_PYTHON" ]]; then
  command -v "$REMOTE_PYTHON" >/dev/null || fail_preflight "requested python missing: $REMOTE_PYTHON"
  REMOTE_PYTHON="$(command -v "$REMOTE_PYTHON")"
elif [[ -x "$OLD_DIR/venv/bin/python" ]]; then
  REMOTE_PYTHON="$OLD_DIR/venv/bin/python"
elif command -v python3 >/dev/null; then
  REMOTE_PYTHON="$(command -v python3)"
else
  fail_preflight "no usable Python found (checked legacy venv and python3)"
fi

"$REMOTE_PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' || fail_preflight "Python 3.11+ required: $REMOTE_PYTHON"
echo "Migration preflight OK: old_dir=$OLD_DIR old_service=$OLD_SERVICE new_dir=$NEW_DIR new_service=$NEW_SERVICE python=$REMOTE_PYTHON ($("$REMOTE_PYTHON" --version 2>&1))"

rollback() {
  rc=$?
  set +e
  echo "Migration failed (rc=$rc); rolling back to $OLD_SERVICE" >&2
  sudo systemctl stop "$NEW_SERVICE" 2>/dev/null || true
  sudo systemctl disable "$NEW_SERVICE" 2>/dev/null || true
  sudo rm -f "/etc/systemd/system/${NEW_SERVICE}.service"
  sudo systemctl daemon-reload || true
  sudo systemctl enable "$OLD_SERVICE" 2>/dev/null || true
  sudo systemctl restart "$OLD_SERVICE" 2>/dev/null || true
  exit "$rc"
}
trap rollback ERR

mkdir -p "$BACKUP_ROOT"
cp -a "$OLD_DIR/shared" "$BACKUP_ROOT/shared"
if [[ -L "$OLD_DIR/current" ]]; then readlink -f "$OLD_DIR/current" > "$BACKUP_ROOT/old-current-target.txt"; fi
sudo systemctl stop "$OLD_SERVICE"

rm -rf "$NEW_DIR.migrating"
mkdir -p "$NEW_DIR.migrating"
git clone --no-checkout "$REPO_URL" "$NEW_DIR.migrating/repo"
git -C "$NEW_DIR.migrating/repo" fetch origin "$MIGRATION_REF" --depth=1
git -C "$NEW_DIR.migrating/repo" checkout --detach FETCH_HEAD
COMMIT_SHA="$(git -C "$NEW_DIR.migrating/repo" rev-parse HEAD)"
mkdir -p "$NEW_DIR.migrating/releases/$COMMIT_SHA" "$NEW_DIR.migrating/shared"
cp -a "$NEW_DIR.migrating/repo/." "$NEW_DIR.migrating/releases/$COMMIT_SHA/"
rm -rf "$NEW_DIR.migrating/releases/$COMMIT_SHA/.git"
cp -a "$OLD_DIR/shared/." "$NEW_DIR.migrating/shared/"
chmod 600 "$NEW_DIR.migrating/shared/.env"
ln -s "releases/$COMMIT_SHA" "$NEW_DIR.migrating/current"
"$REMOTE_PYTHON" -m venv "$NEW_DIR.migrating/venv"
"$NEW_DIR.migrating/venv/bin/python" -m pip install --upgrade pip
"$NEW_DIR.migrating/venv/bin/python" -m pip install "$NEW_DIR.migrating/releases/$COMMIT_SHA"
"$NEW_DIR.migrating/venv/bin/jdss" --config "$NEW_DIR.migrating/current/strategy.yaml" validate-config

if [[ -e "$NEW_DIR" ]]; then mv "$NEW_DIR" "${NEW_DIR}.pre-migration-${STAMP}"; fi
mv "$NEW_DIR.migrating" "$NEW_DIR"
sed -e "s|__SERVER_USER__|$(id -un)|g" -e "s|__TARGET_DIR__|$NEW_DIR|g" "$SERVICE_TEMPLATE" > "/tmp/${NEW_SERVICE}.service"
sudo install -m 0644 "/tmp/${NEW_SERVICE}.service" "/etc/systemd/system/${NEW_SERVICE}.service"
sudo systemctl daemon-reload
sudo systemctl enable "$NEW_SERVICE"
sudo systemctl restart "$NEW_SERVICE"
sudo systemctl is-active --quiet "$NEW_SERVICE"

set -a; source "$NEW_DIR/shared/.env"; set +a
test "${JDSS_TRADING_MODE:-}" = "dry_run"
test -z "${JDSS_LIVE_CONFIRMATION:-}"
"$NEW_DIR/venv/bin/jdss" --config "$NEW_DIR/current/strategy.yaml" init-db
"$NEW_DIR/venv/bin/jdss" --config "$NEW_DIR/current/strategy.yaml" validate-config
"$NEW_DIR/venv/bin/jdss" --config "$NEW_DIR/current/strategy.yaml" toss-smoke
sudo systemctl is-active --quiet "$NEW_SERVICE"

sudo systemctl disable "$OLD_SERVICE" || true
trap - ERR
printf 'Migration successful: %s -> %s, service %s -> %s, commit=%s, backup=%s\n' "$OLD_DIR" "$NEW_DIR" "$OLD_SERVICE" "$NEW_SERVICE" "$COMMIT_SHA" "$BACKUP_ROOT"
