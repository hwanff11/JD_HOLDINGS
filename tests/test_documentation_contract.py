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
        "ONE_PAGE_REPORT.md",
        "STRATEGY_GUIDE.md",
        "HISTORY.md",
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


def test_history_preserves_representative_versions_and_rejected_candidate():
    history = (ROOT / "docs/HISTORY.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/STRATEGY_GUIDE.md").read_text(encoding="utf-8")

    for version in ("v1.1.2", "v2.2.2", "v3.0.0", "v3.2.2"):
        assert version in history
    assert "SEMIMONTHLY_BAND_H05" in history
    assert "MONTHLY_H05" in history
    assert "JDSS V3.2.2" in guide


def test_one_page_report_and_guide_cover_required_plain_language_topics():
    report = (ROOT / "docs/ONE_PAGE_REPORT.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/STRATEGY_GUIDE.md").read_text(encoding="utf-8")

    for required in ("용어 미니 사전", "QQQ 비교", "거래 사이클", "SAFE_MODE", "64.29%"):
        assert required in report
    for required in (
        "flowchart",
        "HWM75",
        "RS6M",
        "2단계",
        "대기 중인 코어 BUY",
        "clientOrderId",
    ):
        assert required in guide
