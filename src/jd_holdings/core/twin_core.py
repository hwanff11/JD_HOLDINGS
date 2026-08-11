from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal

import pandas as pd


@dataclass(frozen=True)
class MonthlyTrendSignal:
    symbol: str
    underlying: str
    trade_date: date
    close: Decimal
    moving_average: Decimal
    active: bool


def monthly_trend_signal(
    symbol: str,
    underlying: str,
    frame: pd.DataFrame,
    *,
    months: int,
) -> MonthlyTrendSignal:
    """Return a completed-month signal without looking into a future session."""
    if months < 2:
        raise ValueError("추세 이동평균은 2개월 이상이어야 합니다")
    if frame.empty or "close" not in frame:
        raise ValueError(f"{underlying} 월간 추세 데이터가 없습니다")
    monthly = frame["close"].dropna().groupby(frame.index.to_period("M")).last()
    if len(monthly) < months:
        raise ValueError(f"{underlying} 월간 추세 계산에 {months}개월이 필요합니다")
    average = monthly.rolling(months, min_periods=months).mean().iloc[-1]
    close = monthly.iloc[-1]
    timestamp = frame[frame.index.to_period("M") == monthly.index[-1]].index[-1]
    return MonthlyTrendSignal(
        symbol=symbol.upper(),
        underlying=underlying.upper(),
        trade_date=timestamp.date(),
        close=Decimal(str(close)),
        moving_average=Decimal(str(average)),
        active=bool(close > average),
    )


def target_quantity(
    equity: Decimal,
    target_weight: Decimal,
    price: Decimal,
    fee: Decimal,
) -> int:
    if equity <= 0 or target_weight <= 0 or price <= 0:
        return 0
    gross = price * (Decimal("1") + fee)
    return int((equity * target_weight / gross).to_integral_value(rounding=ROUND_DOWN))


def is_month_end_session(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> bool:
    if timestamp not in index:
        return False
    period = timestamp.to_period("M")
    return timestamp == index[index.to_period("M") == period][-1]
