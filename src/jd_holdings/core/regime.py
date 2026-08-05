from __future__ import annotations

from .enums import MarketRegime
from .models import IndicatorSnapshot


def evaluate_regime(spy: IndicatorSnapshot, qqq: IndicatorSnapshot) -> MarketRegime:
    if spy.trade_date != qqq.trade_date:
        raise ValueError("SPY와 QQQ의 최신 거래일이 다릅니다")
    green_conditions = (
        float(spy.close) > spy.ema60,
        float(qqq.close) > qqq.ema60,
        spy.ema20 > spy.ema60,
        qqq.ema20 > qqq.ema60,
    )
    if all(green_conditions):
        return MarketRegime.GREEN
    if float(spy.close) < spy.ema60 and float(qqq.close) < qqq.ema60:
        return MarketRegime.RED
    return MarketRegime.YELLOW
