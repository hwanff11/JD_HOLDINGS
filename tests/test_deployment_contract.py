from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_deploy_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", str(ROOT / "deploy.sh")], check=True)


def test_systemd_uses_release_venv_and_shared_writable_runtime_paths():
    service = (ROOT / "systemd/jh_holdings_bot.service.template").read_text(encoding="utf-8")
    assert "ExecStart=__TARGET_DIR__/current/.venv/bin/jdss-bot" in service
    assert "JDSS_DB_PATH=__TARGET_DIR__/shared/data/jdss.db" in service
    assert "JDSS_LOG_PATH=__TARGET_DIR__/shared/logs/jdss.log" in service
    assert "JDSS_CACHE_PATH=__TARGET_DIR__/shared/data/cache" in service
    assert "JDSS_CONFIG_PATH=__TARGET_DIR__/current/strategy.yaml" in service
    assert "ReadWritePaths=__TARGET_DIR__/shared" in service
    assert "UMask=0077" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateDevices=true" in service
    assert "ProtectKernelTunables=true" in service
    assert "ProtectKernelModules=true" in service
    assert "ProtectControlGroups=true" in service
    assert "RestrictSUIDSGID=true" in service
    assert "LockPersonality=true" in service
    assert "CapabilityBoundingSet=" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in service


def test_deploy_requires_exact_remote_main_and_pinned_ssh_host_key():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert '"$(git branch --show-current)" != "main"' in deploy
    assert '"$(git rev-parse HEAD)" != "$(git rev-parse origin/main)"' in deploy
    assert "SSH_KNOWN_HOSTS_PATH" in deploy
    assert "StrictHostKeyChecking=yes" in deploy
    assert "UserKnownHostsFile=" in deploy
    assert "StrictHostKeyChecking=accept-new" not in deploy
    assert "ssh-keyscan" not in deploy
    assert '"$shared_dir/data/cache"' in deploy
    assert '"$shared_dir/backups"' in deploy
    assert '"$shared_dir/.env"' in deploy
    assert "SKIP_LOCAL_CHECKS" in deploy
    assert "JDSS_TRADING_MODE=dry_run" in deploy
    assert "JDSS_LIVE_CONFIRMATION=" in deploy
    assert "toss-smoke" in deploy
    assert "git push origin main || true" not in deploy


def test_deploy_prepares_release_before_stop_and_uses_release_local_venv():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    install_pos = deploy.index('"$remote_python" -m venv "$release_dir/.venv"')
    # The rollback function also contains a stop command; the final occurrence is
    # the actual deployment cutover after the release has been prepared.
    stop_pos = deploy.rindex('sudo systemctl stop "$service_name"')
    assert install_pos < stop_pos
    assert '"$release_dir/.venv/bin/python" -m pip install "$release_dir"' in deploy
    assert '"$target_dir/current/.venv/bin/jdss"' in deploy
    assert 'test -x "$target_dir/current/.venv/bin/jdss-bot"' in deploy
    assert '"$target_dir/venv/bin/jdss"' not in deploy


def test_deploy_takes_sqlite_snapshot_and_rolls_back_runtime_on_failure():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "source.backup(target)" in deploy
    assert 'backup_path="$shared_dir/backups/jdss-' in deploy
    assert "rollback_armed=1" in deploy
    assert "trap rollback ERR" in deploy
    assert 'ln -sfn "$previous_current" "$target_dir/current.rollback"' in deploy
    assert 'sudo install -m 0644 "$unit_backup" "$service_unit"' in deploy
    assert 'cp "$backup_path" "$db_path"' in deploy
    assert "자동 rollback 성공" in deploy
    assert "자동 rollback 실패" in deploy


def test_standard_deploy_never_crosses_config_generation_or_deletes_db():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "old_version == new_version" in deploy
    assert "표준 deploy에서 금지" in deploy
    assert "별도 migration plan·DB 백업·" in deploy
    assert "PENDING_CANCEL" in deploy
    assert "PENDING_REPLACE" in deploy
    assert "db_path.unlink()" not in deploy
    assert "v322-migration" not in deploy


def test_github_deploy_attaches_verified_sha_to_local_main():
    workflow = (ROOT / ".github/workflows/deploy-oracle-dry-run.yml").read_text(
        encoding="utf-8"
    )
    assert 'test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"' in workflow
    assert "git checkout -B main HEAD" in workflow
    assert "ref: main" in workflow
    assert 'SKIP_LOCAL_CHECKS: "1"' in workflow
    assert "issues:" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "[deploy-oracle-dry-run]" in workflow
    assert "inputs.ref" not in workflow
    assert "gh issue comment" in workflow


def test_github_deploy_requires_pinned_oracle_known_hosts():
    workflow = (ROOT / ".github/workflows/deploy-oracle-dry-run.yml").read_text(
        encoding="utf-8"
    )
    assert "ORACLE_SSH_KNOWN_HOSTS: ${{ secrets.ORACLE_SSH_KNOWN_HOSTS }}" in workflow
    assert "SSH_KNOWN_HOSTS_PATH: /home/runner/.ssh/known_hosts" in workflow
    assert 'ssh-keygen -F "$ORACLE_HOST"' in workflow
    assert "accept-new" not in workflow
    assert "ssh-keyscan" not in workflow


def test_github_deploy_validates_current_v322_contract():
    workflow = (ROOT / ".github/workflows/deploy-oracle-dry-run.yml").read_text(
        encoding="utf-8"
    )
    assert "JDSS-3.2.2-RS6M-ONEWAY-HWM75" in workflow
    assert 'config_version: "3.2.2"' in workflow
    assert "total_capital: 50000" in workflow
    assert "hwm_reinvestment_fraction: 0.75" in workflow
    assert "rs_lookback: 126" in workflow
    assert "jdss_overlay_weight: 0.05" in workflow
    assert "live_enabled: false" in workflow


def test_runtime_verifier_uses_oracle_environment_secrets_and_release_venv():
    workflow = (ROOT / ".github/workflows/verify-oracle-v322-runtime.yml").read_text(
        encoding="utf-8"
    )
    assert "environment: oracle-dry-run" in workflow
    assert "ORACLE_SSH_KEY: ${{ secrets.ORACLE_SSH_KEY }}" in workflow
    assert "ORACLE_SSH_KNOWN_HOSTS: ${{ secrets.ORACLE_SSH_KNOWN_HOSTS }}" in workflow
    assert "ORACLE_HOST: ${{ secrets.ORACLE_HOST }}" in workflow
    assert 'env_file="$TARGET_DIR/shared/.env"' in workflow
    assert 'source "$env_file"' in workflow
    assert '"$current/.venv/bin/jdss"' in workflow
    assert '"$current/.venv/bin/python"' in workflow
    assert '"$TARGET_DIR/venv/bin/jdss"' not in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "accept-new" not in workflow


def test_runtime_verifier_checks_release_directory_sha_without_git_metadata():
    workflow = (ROOT / ".github/workflows/verify-oracle-v322-runtime.yml").read_text(
        encoding="utf-8"
    )
    assert 'deployed_sha="$(basename "$current")"' in workflow
    assert 'test "$current" = "$TARGET_DIR/releases/$EXPECTED_SHA"' in workflow
    assert 'git -C "$current" rev-parse HEAD' not in workflow


def test_runtime_verifier_uses_current_market_clock_api():
    workflow = (ROOT / ".github/workflows/verify-oracle-v322-runtime.yml").read_text(
        encoding="utf-8"
    )
    assert "MarketClock().classify_session()" in workflow
    assert "MarketClock().phase()" not in workflow
    assert "closed|pre_market|regular|after_hours" in workflow


def test_canonical_workflow_set_is_small_and_completed_migration_is_retired():
    workflow_dir = ROOT / ".github/workflows"
    expected = {
        "ci.yml",
        "deploy-oracle-dry-run.yml",
        "jdss-backtest.yml",
        "security.yml",
        "verify-oracle-v322-runtime.yml",
    }
    assert {path.name for path in workflow_dir.glob("*.yml")} == expected
    assert not (ROOT / ".github/workflows/migrate-oracle-runtime-to-jh.yml").exists()
    assert not (ROOT / "scripts/migrate_oracle_runtime_to_jh.sh").exists()


def test_workflows_use_node24_actions():
    for name in (
        "ci.yml",
        "deploy-oracle-dry-run.yml",
        "jdss-backtest.yml",
        "security.yml",
        "verify-oracle-v322-runtime.yml",
    ):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "actions/checkout@v7" in workflow
        assert "actions/setup-python@v7" in workflow
        assert "actions/checkout@v4" not in workflow
        assert "actions/setup-python@v5" not in workflow


def test_security_workflow_uploads_codeql_and_audits_main_protection():
    workflow = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    assert "github/codeql-action/analyze@v4" in workflow
    assert "upload: false" not in workflow
    assert "Security Gate" in workflow
    assert "Main branch protection" in workflow
    assert "gh api" in workflow
    assert "main branch protection/ruleset is not enabled" in workflow


def test_quality_gate_enforces_minimum_coverage():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--cov-fail-under=70" in workflow
    assert "--cov-fail-under=0" not in workflow
