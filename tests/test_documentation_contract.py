from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_markdown_relative_links_resolve():
    missing: list[str] = []
    for document in ROOT.rglob("*.md"):
        for target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            raw = target.split("#", 1)[0].strip()
            if not raw or raw.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / raw).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    assert missing == []


def test_document_roles_and_change_impact_are_explicit():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/README.md").read_text(encoding="utf-8")

    for required in (
        "변경 영향별 필수 동기화",
        "strategy.yaml",
        "JDSS_FINAL_SPEC.md",
        "TELEGRAM_BOT_GUIDE.md",
        "DEPLOYMENT.md",
        "SECURITY.md",
        "CURRENT_WORK.md",
    ):
        assert required in agents
    for required in (
        "JDSS_FINAL_SPEC.md",
        "TELEGRAM_BOT_GUIDE.md",
        "DEPLOYMENT.md",
        "DEVELOPMENT_WORKFLOW.md",
        "SECURITY.md",
        "DECISIONS.md",
        "STRATEGY_GUIDE.md",
        "BACKTEST_REPORT.md",
    ):
        assert required in guide


def test_mutable_runtime_status_has_single_source():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_readme = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs/infra/DEVELOPMENT_WORKFLOW.md").read_text(encoding="utf-8")

    assert "현재 브랜치·Oracle 배포·검증 상태" in readme
    assert "변동 상태 기록 원칙" in docs_readme
    assert "활성 개발 브랜치, 최신 `main` SHA" in workflow
    assert "## 현재 운영 기준" not in docs_readme
    assert "## 현재 활성 개발 브랜치" not in workflow


def test_legacy_strategy_config_is_clearly_archived():
    legacy = (ROOT / "configs/strategy_v1.1.2.yaml").read_text(encoding="utf-8")
    assert legacy.startswith("# ARCHIVE ONLY:")
    assert "저장소 루트 strategy.yaml만 사용" in legacy
