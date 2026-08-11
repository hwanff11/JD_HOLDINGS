from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_twin_rebalance_frequency import (  # noqa: E402
    CADENCES,
    _cadence_sessions,
    _paired_bootstrap,
)


def test_semimonthly_cadence_uses_last_completed_session_on_or_before_15th():
    sessions = pd.bdate_range("2026-01-02", "2026-02-27")

    selected = _cadence_sessions(sessions, CADENCES["SEMIMONTHLY_BAND"])

    assert selected == {pd.Timestamp("2026-01-15"), pd.Timestamp("2026-02-13")}


def test_monthly_cadence_adds_no_intramonth_rebalance_sessions():
    sessions = pd.bdate_range("2026-01-02", "2026-02-27")

    assert _cadence_sessions(sessions, CADENCES["MONTHLY"]) == set()


def test_paired_bootstrap_is_deterministic_and_detects_strict_dominance():
    index = pd.bdate_range("2024-01-02", periods=120)
    baseline = pd.Series((1.001**position for position in range(120)), index=index)
    candidate = pd.Series((1.002**position for position in range(120)), index=index)

    first = _paired_bootstrap(candidate, baseline, iterations=100, block=20)
    second = _paired_bootstrap(candidate, baseline, iterations=100, block=20)

    assert first == second
    assert first["return_win_pct"] == 100.0
    assert first["mdd_win_pct"] == 0.0
