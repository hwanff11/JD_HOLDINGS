from __future__ import annotations

import math

from jd_holdings.config import StrategyConfig

from .enums import MarketRegime, SignalGrade
from .models import IndicatorSnapshot, ScoreResult

_COMPONENT_MAXIMA = {
    "regime": 25,
    "oversold": 40,
    "reversal": 20,
    "volume": 10,
    "atr": 5,
}


def _lte_band_score(value: float, bands: list[list[float | int]]) -> int:
    for threshold, score in bands:
        if value <= float(threshold):
            return int(score)
    return 0


def _gte_band_score(value: float, bands: list[list[float | int]]) -> int:
    score = 0
    for threshold, candidate in bands:
        if value >= float(threshold):
            score = int(candidate)
    return score


def calculate_grade(total: int, config: StrategyConfig) -> SignalGrade:
    grades = config.scoring["grades"]
    if total >= int(grades["S"]):
        return SignalGrade.S
    if total >= int(grades["A"]):
        return SignalGrade.A
    if total >= int(grades["B"]):
        return SignalGrade.B
    if total >= int(grades["WATCH"]):
        return SignalGrade.WATCH
    return SignalGrade.NO_TRADE


def _calibrate_component(
    component: str,
    raw_score: int,
    config: StrategyConfig,
) -> int:
    calibration = config.scoring.get("calibration", {})
    if not calibration.get("enabled", False):
        return raw_score
    exponent = float(calibration.get("exponents", {}).get(component, 1.0))
    maximum = _COMPONENT_MAXIMA[component]
    if raw_score <= 0:
        return 0
    if raw_score >= maximum:
        return maximum
    calibrated = maximum * (raw_score / maximum) ** exponent
    return min(maximum, math.floor(calibrated + 0.5))


def calculate_score(
    snapshot: IndicatorSnapshot,
    regime: MarketRegime,
    config: StrategyConfig,
) -> ScoreResult:
    regime_scores = {
        MarketRegime.GREEN: int(config.market_regime["green_score"]),
        MarketRegime.YELLOW: int(config.market_regime["yellow_score"]),
        MarketRegime.RED: int(config.market_regime["red_score"]),
    }
    regime_score = regime_scores[regime]

    oversold_score = _lte_band_score(snapshot.cci5, config.scoring["cci5"]["bands"])
    oversold_score += _lte_band_score(snapshot.cci10, config.scoring["cci10"]["bands"])
    oversold_score += _lte_band_score(snapshot.rsi5, config.scoring["rsi5"]["bands"])
    oversold_score += _lte_band_score(snapshot.rsi14, config.scoring["rsi14"]["bands"])
    bollinger = config.scoring["bollinger"]
    close = float(snapshot.close)
    if close <= snapshot.bb_lower * float(bollinger["deep_multiplier"]):
        oversold_score += int(bollinger["deep_score"])
    elif close <= snapshot.bb_lower:
        oversold_score += int(bollinger["touch_score"])
    oversold_score = min(40, oversold_score)

    reversal_conditions = (
        snapshot.close > snapshot.open,
        snapshot.close > snapshot.previous_close,
        close > snapshot.ema5,
        snapshot.close_position >= float(config.scoring["reversal_close_position_threshold"]),
    )
    reversal_score = sum(reversal_conditions) * int(config.scoring["reversal_points_per_condition"])

    volume_score = _gte_band_score(snapshot.volume_ratio, config.scoring["volume_bands"])
    atr = config.scoring["atr_bands"]
    atr_scores = [int(value) for value in atr["scores"]]
    if snapshot.atr_pct < float(atr["low_1"]):
        atr_score = atr_scores[0]
    elif snapshot.atr_pct < float(atr["low_2"]):
        atr_score = atr_scores[1]
    elif snapshot.atr_pct <= float(atr["high"]):
        atr_score = atr_scores[2]
    else:
        atr_score = atr_scores[3]

    raw_scores = {
        "regime": regime_score,
        "oversold": oversold_score,
        "reversal": reversal_score,
        "volume": volume_score,
        "atr": atr_score,
    }
    calibrated_scores = {
        component: _calibrate_component(component, score, config)
        for component, score in raw_scores.items()
    }
    regime_score = calibrated_scores["regime"]
    oversold_score = calibrated_scores["oversold"]
    reversal_score = calibrated_scores["reversal"]
    volume_score = calibrated_scores["volume"]
    atr_score = calibrated_scores["atr"]
    total = regime_score + oversold_score + reversal_score + volume_score + atr_score
    total = max(0, min(100, total))
    return ScoreResult(
        total=total,
        grade=calculate_grade(total, config),
        regime=regime,
        regime_score=regime_score,
        oversold_score=oversold_score,
        reversal_score=reversal_score,
        volume_score=volume_score,
        atr_score=atr_score,
        raw_regime_score=raw_scores["regime"],
        raw_oversold_score=raw_scores["oversold"],
        raw_reversal_score=raw_scores["reversal"],
        raw_volume_score=raw_scores["volume"],
        raw_atr_score=raw_scores["atr"],
    )
