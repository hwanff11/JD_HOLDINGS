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
fail_migration() { echo "Migration validation failed: $*" >&2; return 1; }
step() { echo "==> $*"; }

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

step "Backing up legacy shared runtime"
mkdir -p "$BACKUP_ROOT"
cp -a "$OLD_DIR/shared" "$BACKUP_ROOT/shared"
if [[ -L "$OLD_DIR/current" ]]; then readlink -f "$OLD_DIR/current" > "$BACKUP_ROOT/old-current-target.txt"; fi
sudo systemctl stop "$OLD_SERVICE"

step "Staging repository and shared runtime"
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

# Rewrite only the known legacy runtime root. Secrets and unrelated values are untouched.
if grep -Fq "$OLD_DIR" "$NEW_DIR.migrating/shared/.env"; then
  step "Rewriting legacy absolute runtime paths in shared/.env"
  sed -i "s|$OLD_DIR|$NEW_DIR|g" "$NEW_DIR.migrating/shared/.env"
fi
if grep -Fq "$OLD_DIR" "$NEW_DIR.migrating/shared/.env"; then
  fail_migration "legacy runtime path remains in shared/.env"
fi
ln -s "releases/$COMMIT_SHA" "$NEW_DIR.migrating/current"

if [[ -e "$NEW_DIR" ]]; then mv "$NEW_DIR" "${NEW_DIR}.pre-migration-${STAMP}"; fi
mv "$NEW_DIR.migrating" "$NEW_DIR"

step "Creating virtualenv at final runtime path"
"$REMOTE_PYTHON" -m venv "$NEW_DIR/venv"
"$NEW_DIR/venv/bin/python" -m pip install --upgrade pip
"$NEW_DIR/venv/bin/python" -m pip install "$NEW_DIR/releases/$COMMIT_SHA"
"$NEW_DIR/venv/bin/jdss" --config "$NEW_DIR/current/strategy.yaml" validate-config || fail_migration "config validation failed before service start"

step "Installing and starting $NEW_SERVICE"
sed -e "s|__SERVER_USER__|$(id -un)|g" -e "s|__TARGET_DIR__|$NEW_DIR|g" "$SERVICE_TEMPLATE" > "/tmp/${NEW_SERVICE}.service"
sudo install -m 0644 "/tmp/${NEW_SERVICE}.service" "/etc/systemd/system/${NEW_SERVICE}.service"
sudo systemctl daemon-reload
sudo systemctl enable "$NEW_SERVICE"
sudo systemctl restart "$NEW_SERVICE"
sudo systemctl is-active --quiet "$NEW_SERVICE" || fail_migration "$NEW_SERVICE did not become active"

step "Validating migrated environment"
set -a; source "$NEW_DIR/shared/.env"; set +a
[[ "${JDSS_TRADING_MODE:-}" == "dry_run" ]] || fail_migration "JDSS_TRADING_MODE must remain dry_run (actual=${JDSS_TRADING_MODE:-unset})"
[[ -z "${JDSS_LIVE_CONFIRMATION:-}" ]] || fail_migration "JDSS_LIVE_CONFIRMATION must remain empty"
[[ "${JDSS_DB_PATH:-$NEW_DIR/shared/data/jdss.db}" == "$NEW_DIR/shared/data/jdss.db" ]] || fail_migration "unexpected JDSS_DB_PATH=${JDSS_DB_PATH:-unset}"
[[ "${JDSS_LOG_PATH:-$NEW_DIR/shared/logs/jdss.log}" == "$NEW_DIR/shared/logs/jdss.log" ]] || fail_migration "unexpected JDSS_LOG_PATH=${JDSS_LOG_PATH:-unset}"
[[ "${JDSS_CACHE_PATH:-$NEW_DIR/shared/data/cache}" == "$NEW_DIR/shared/data/cache" ]] || fail_migration "unexpected JDSS_CACHE_PATH=${JDSS_CACHE_PATH:-unset}"
[[ -f "$NEW_DIR/shared/data/jdss.db" ]] || fail_migration "migrated DB missing at $NEW_DIR/shared/data/jdss.db"

step "Running DB/config/Toss read-only validations"
"$NEW_DIR/venv/bin/jdss" --config "$NEW_DIR/current/strategy.yaml" init-db || fail_migration "init-db failed"
"$NEW_DIR/venv/bin/jdss" --config "$NEW_DIR/current/strategy.yaml" validate-config || fail_migration "final config validation failed"
"$NEW_DIR/venv/bin/jdss" --config "$NEW_DIR/current/strategy.yaml" toss-smoke || fail_migration "Toss read-only smoke test failed"
sudo systemctl is-active --quiet "$NEW_SERVICE" || fail_migration "$NEW_SERVICE became inactive after validation"

step "Retiring legacy service"
sudo systemctl disable "$OLD_SERVICE" || fail_migration "failed to disable legacy service $OLD_SERVICE"
trap - ERR
printf 'Migration successful: %s -> %s, service %s -> %s, commit=%s, backup=%s\n' "$OLD_DIR" "$NEW_DIR" "$OLD_SERVICE" "$NEW_SERVICE" "$COMMIT_SHA" "$BACKUP_ROOT"
