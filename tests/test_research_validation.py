from __future__ import annotations

from dataclasses import replace

import pytest

from jd_holdings.backtest.research_validation import (
    StageThresholds,
    assert_signal_threshold_contract,
    assert_threshold_change_has_effect_when_binding,
    assert_threshold_override,
)
from jd_holdings.config import load_config


def _config_with_thresholds(s1: int, s2: int, s3: int):
    config = load_config("strategy.yaml")
    stages = {
        2: replace(config.additional_entry.stages[2], min_score=s2),
        3: replace(config.additional_entry.stages[3], min_score=s3),
    }
    return replace(
        config,
        global_=replace(config.global_, entry_score=s1),
        additional_entry=replace(config.additional_entry, stages=stages),
    )


def test_stage_thresholds_read_integer_stage_keys() -> None:
    candidate = _config_with_thresholds(55, 60, 50)
    assert StageThresholds.from_config(candidate) == StageThresholds(55, 60, 50)


def test_threshold_override_detects_requested_values() -> None:
    baseline = load_config("strategy.yaml")
    candidate = _config_with_thresholds(55, 60, 50)
    assert_threshold_override(baseline, candidate, StageThresholds(55, 60, 50))


def test_threshold_override_rejects_unapplied_candidate() -> None:
    baseline = load_config("strategy.yaml")
    with pytest.raises(AssertionError, match="threshold override mismatch"):
        assert_threshold_override(baseline, baseline, StageThresholds(55, 60, 50))


def test_signal_contract_rejects_signal_below_stage_floor() -> None:
    signals = [
        {"trade_date": "2026-01-02", "target_stage": 2, "score": 59, "symbol": "SOXL"}
    ]
    with pytest.raises(AssertionError, match="signal below configured floor"):
        assert_signal_threshold_contract(signals, StageThresholds(55, 60, 50))


def test_signal_contract_accepts_valid_stages() -> None:
    signals = [
        {"trade_date": "2026-01-02", "target_stage": 1, "score": 55},
        {"trade_date": "2026-01-03", "target_stage": 2, "score": 60},
        {"trade_date": "2026-01-04", "target_stage": 3, "score": 50},
    ]
    assert_signal_threshold_contract(signals, StageThresholds(55, 60, 50))


def test_binding_stricter_floor_cannot_leave_identical_outputs() -> None:
    baseline_signals = [
        {"trade_date": "2024-07-26", "target_stage": 2, "score": 59, "symbol": "SOXL"}
    ]
    with pytest.raises(AssertionError, match="binding threshold changed"):
        assert_threshold_change_has_effect_when_binding(
            baseline_signals=baseline_signals,
            candidate_signals=baseline_signals,
            baseline_thresholds=StageThresholds(55, 55, 55),
            candidate_thresholds=StageThresholds(55, 60, 55),
            baseline_trades=[{"action": "ADD_ENTRY"}],
            candidate_trades=[{"action": "ADD_ENTRY"}],
        )


def test_nonbinding_floor_may_legitimately_leave_outputs_identical() -> None:
    signals = [
        {"trade_date": "2024-07-26", "target_stage": 2, "score": 68, "symbol": "SOXL"}
    ]
    assert_threshold_change_has_effect_when_binding(
        baseline_signals=signals,
        candidate_signals=signals,
        baseline_thresholds=StageThresholds(55, 55, 55),
        candidate_thresholds=StageThresholds(55, 60, 55),
    )
