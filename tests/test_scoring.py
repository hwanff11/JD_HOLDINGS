from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from conftest import make_snapshot

from jd_holdings.core.enums import MarketRegime, SignalGrade
from jd_holdings.core.regime import evaluate_regime
from jd_holdings.core.scoring import calculate_score


@pytest.mark.parametrize(
    ("cci", "expected"),
    [(-99.99, 0), (-100, 6), (-150, 10), (-200, 13), (-250, 13)],
)
def test_cci_boundaries(baseline_config, cci, expected):
    snapshot = make_snapshot(
        cci5=cci,
        cci10=0,
        rsi5=50,
        rsi14=50,
        close=Decimal("110"),
        bb_lower=100,
        open=Decimal("111"),
        previous_close=Decimal("111"),
        ema5=111,
        close_position=0,
        volume_ratio=0.5,
        atr_pct=0.005,
    )
    result = calculate_score(snapshot, MarketRegime.RED, baseline_config)
    assert result.oversold_score == expected


def test_score_components_and_grade(baseline_config):
    snapshot = make_snapshot()
    result = calculate_score(snapshot, MarketRegime.GREEN, baseline_config)
    assert result.regime_score == 25
    assert result.reversal_score == 10
    assert result.oversold_score == 31
    assert result.volume_score == 7
    assert result.atr_score == 5
    assert result.total == 78
    assert result.grade == SignalGrade.WATCH


def test_v1_3_1_calibration_keeps_raw_audit_without_near_max_inflation(config):
    result = calculate_score(make_snapshot(), MarketRegime.GREEN, config)
    assert result.total == 84
    assert result.grade == SignalGrade.B
    assert result.oversold_score == 34
    assert result.reversal_score == 12
    assert result.volume_score == 8
    assert result.detail()["raw_components"] == {
        "regime_score": 25,
        "oversold_score": 31,
        "reversal_score": 10,
        "volume_score": 7,
        "atr_score": 5,
    }
    assert result.detail()["calibration_applied"] is True


def test_power_calibration_preserves_maxima_and_grade_boundaries(baseline_config):
    calibrated = replace(
        baseline_config,
        scoring={
            **baseline_config.scoring,
            "calibration": {
                "enabled": True,
                "method": "power",
                "exponents": {
                    "regime": 1.0,
                    "oversold": 0.5,
                    "reversal": 0.5,
                    "volume": 0.5,
                    "atr": 1.0,
                },
            },
        },
    )
    result = calculate_score(make_snapshot(), MarketRegime.GREEN, calibrated)
    assert result.total == 87
    assert result.grade == SignalGrade.B
    assert result.oversold_score == 35
    assert result.reversal_score == 14
    assert result.volume_score == 8
    assert result.detail()["raw_components"]["oversold_score"] == 31
    assert result.detail()["calibration_applied"] is True


def test_unit_exponents_reproduce_v1_1_2(baseline_config):
    calibrated = replace(
        baseline_config,
        scoring={
            **baseline_config.scoring,
            "calibration": {
                "enabled": True,
                "method": "power",
                "exponents": {
                    "regime": 1.0,
                    "oversold": 1.0,
                    "reversal": 1.0,
                    "volume": 1.0,
                    "atr": 1.0,
                },
            },
        },
    )
    baseline = calculate_score(make_snapshot(), MarketRegime.GREEN, baseline_config)
    result = calculate_score(make_snapshot(), MarketRegime.GREEN, calibrated)
    assert result.total == baseline.total
    assert result.detail()["raw_components"] == baseline.detail()["raw_components"]
    assert result.detail()["calibration_applied"] is False


def test_market_regime_boundaries():
    spy = make_snapshot(symbol="SPY", close=Decimal("120"), ema20=115, ema60=110)
    qqq = make_snapshot(symbol="QQQ", close=Decimal("130"), ema20=125, ema60=120)
    assert evaluate_regime(spy, qqq) == MarketRegime.GREEN
    assert (
        evaluate_regime(
            make_snapshot(symbol="SPY", close=Decimal("100"), ema20=105, ema60=110),
            make_snapshot(symbol="QQQ", close=Decimal("100"), ema20=105, ema60=110),
        )
        == MarketRegime.RED
    )
