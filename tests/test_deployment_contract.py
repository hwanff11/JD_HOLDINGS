from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_systemd_uses_shared_writable_runtime_paths():
    service = (ROOT / "systemd/jd_holdings_bot.service.template").read_text(encoding="utf-8")
    assert "JDSS_DB_PATH=__TARGET_DIR__/shared/data/jdss.db" in service
    assert "JDSS_LOG_PATH=__TARGET_DIR__/shared/logs/jdss.log" in service
    assert "JDSS_CACHE_PATH=__TARGET_DIR__/shared/data/cache" in service
    assert "JDSS_CONFIG_PATH=__TARGET_DIR__/current/strategy.yaml" in service
    assert "ReadWritePaths=__TARGET_DIR__/shared" in service


def test_deploy_requires_exact_remote_main_and_creates_shared_cache():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert '"$(git branch --show-current)" != "main"' in deploy
    assert '"$(git rev-parse HEAD)" != "$(git rev-parse origin/main)"' in deploy
    assert '"$target_dir/shared/data/cache"' in deploy
    assert "SKIP_LOCAL_CHECKS" in deploy
    assert "JDSS_TRADING_MODE=dry_run" in deploy
    assert "JDSS_LIVE_CONFIRMATION=" in deploy
    assert "toss-smoke" in deploy
    assert deploy.count('sudo systemctl restart "$service_name"') == 1
    assert 'pip install --upgrade pip' in deploy
    assert "git push origin main || true" not in deploy


def test_github_deploy_attaches_verified_sha_to_local_main():
    workflow = (ROOT / ".github/workflows/deploy-oracle-dry-run.yml").read_text(
        encoding="utf-8"
    )
    assert "git merge-base --is-ancestor HEAD origin/main" in workflow
    assert "git checkout -B main HEAD" in workflow
    assert 'SKIP_LOCAL_CHECKS: "1"' in workflow
    assert "Force server trading lock to dry_run" not in workflow
    assert "Toss read-only smoke test" not in workflow
    assert "issues:" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "[deploy-oracle-dry-run]" in workflow
    assert "[0-9a-fA-F]{40}" in workflow
    assert "gh issue comment" in workflow
    assert "github.run_id" in workflow
    assert "actions/runs/" in workflow


def test_workflows_use_node24_actions():
    for name in ("ci.yml", "final-dry-run.yml", "deploy-oracle-dry-run.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "actions/checkout@v7" in workflow
        assert "actions/setup-python@v7" in workflow
        assert "actions/checkout@v4" not in workflow
        assert "actions/setup-python@v5" not in workflow
