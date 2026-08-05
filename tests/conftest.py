from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from jd_holdings.config import load_config
from jd_holdings.core.enums import MarketRegime, SignalGrade
from jd_holdings.core.models import IndicatorSnapshot, ScoreResult


@pytest.fixture
def config():
    return load_config(Path(__file__).parents[1] / "strategy.yaml")


@pytest.fixture
def baseline_config():
    return load_config(Path(__file__).parents[1] / "configs" / "strategy_v1.1.2.yaml")


def make_snapshot(**overrides) -> IndicatorSnapshot:
    values = {
        "symbol": "TQQQ",
        "trade_date": date(2026, 8, 4),
        "open": Decimal("99"),
        "high": Decimal("101"),
        "low": Decimal("95"),
        "close": Decimal("100"),
        "previous_close": Decimal("101"),
        "volume": 2_000_000,
        "cci5": -150.0,
        "cci10": -150.0,
        "rsi5": 25.0,
        "rsi14": 35.0,
        "ema5": 101.0,
        "ema20": 105.0,
        "ema60": 110.0,
        "bb_lower": 101.0,
        "atr14": 5.0,
        "atr_pct": 0.05,
        "volume_ratio": 1.5,
        "close_position": 0.8,
    }
    values.update(overrides)
    return IndicatorSnapshot(**values)


def make_score(
    total: int,
    *,
    reversal: int = 5,
    regime: MarketRegime = MarketRegime.GREEN,
) -> ScoreResult:
    grade = (
        SignalGrade.S
        if total >= 92
        else SignalGrade.A
        if total >= 88
        else SignalGrade.B
        if total >= 82
        else SignalGrade.WATCH
        if total >= 76
        else SignalGrade.NO_TRADE
    )
    return ScoreResult(
        total=total,
        grade=grade,
        regime=regime,
        regime_score=25 if regime == MarketRegime.GREEN else 15,
        oversold_score=max(0, total - 25 - reversal),
        reversal_score=reversal,
        volume_score=0,
        atr_score=0,
    )
