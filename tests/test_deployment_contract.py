from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_deploy_script_is_strict_and_dry_run_locked():
    script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in script
    assert "StrictHostKeyChecking=yes" in script
    assert 'UserKnownHostsFile=$SSH_KNOWN_HOSTS_PATH' in script
    assert "accept-new" not in script
    assert "ssh-keyscan" not in script
    assert "JDSS_TRADING_MODE=dry_run" in script
    assert "JDSS_LIVE_CONFIRMATION=" in script
    assert "live_enabled: false" in script


def test_deploy_uses_release_local_venv_and_atomic_current_switch():
    script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert 'release_dir="$target_dir/releases/$commit_sha"' in script
    assert '"$remote_python" -m venv "$release_dir/.venv"' in script
    assert '"$release_dir/.venv/bin/python" -m pip install "$release_dir"' in script
    assert 'ln -sfn "$release_dir" "$target_dir/current.new"' in script
    assert 'mv -Tf "$target_dir/current.new" "$target_dir/current"' in script


def test_deploy_has_database_snapshot_and_automatic_rollback():
    script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "source.backup(target)" in script
    assert 'backup_path="$shared_dir/backups/jdss-' in script
    assert "rollback()" in script
    assert "trap rollback ERR" in script
    assert 'cp "$backup_path" "$db_path"' in script
    assert 'ln -sfn "$previous_current" "$target_dir/current.rollback"' in script


def test_deploy_refuses_standard_cross_version_migration():
    script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "전략 버전 변경은 표준 deploy에서 금지됩니다" in script
    assert "active_positions" in script
    assert "open_orders" in script


def test_github_deploy_is_latest_main_only_and_chatops_owner_only():
    workflow = (ROOT / ".github/workflows/deploy-oracle-dry-run.yml").read_text(
        encoding="utf-8"
    )
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
    # `ssh-keyscan -H` produces hashed hostnames, so the preflight validates
    # the pinned key syntax instead of looking up the clear-text hostname.
    # The real scp/ssh call remains the authoritative hostname/key match check.
    assert "grep -Eq" in workflow
    assert "(ssh-|ecdsa-)" in workflow
    assert "StrictHostKeyChecking=yes" in (ROOT / "deploy.sh").read_text(encoding="utf-8")
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
    assert "rs_sleeve_fraction: 0.50" in workflow
    assert "jdss_overlay_weight: 0.05" in workflow
    assert "live_enabled: false" in workflow
