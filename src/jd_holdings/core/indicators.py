from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from jd_holdings.config import StrategyConfig

from .models import IndicatorSnapshot

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


class MarketDataError(ValueError):
    """Raised when OHLCV data cannot safely be used by the strategy."""


def normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a chronological, lowercase, numeric OHLCV frame."""
    if frame is None or frame.empty:
        raise MarketDataError("OHLCV 데이터가 비어 있습니다")
    result = frame.copy()
    if isinstance(result.columns, pd.MultiIndex):
        if len(result.columns.get_level_values(-1).unique()) == 1:
            result.columns = result.columns.get_level_values(0)
        else:
            raise MarketDataError("한 번에 한 종목의 OHLCV만 처리할 수 있습니다")
    result.columns = [str(column).strip().lower().replace(" ", "_") for column in result.columns]
    aliases = {
        "openprice": "open",
        "highprice": "high",
        "lowprice": "low",
        "closeprice": "close",
        "timestamp": "date",
        "datetime": "date",
    }
    result = result.rename(columns=aliases)
    if "date" in result.columns:
        result["date"] = pd.to_datetime(result["date"], utc=True).dt.tz_convert(None)
        result = result.set_index("date")
    if not isinstance(result.index, pd.DatetimeIndex):
        result.index = pd.to_datetime(result.index, utc=True).tz_convert(None)
    result = result.sort_index()
    result = result[~result.index.duplicated(keep="last")]
    missing = [column for column in REQUIRED_COLUMNS if column not in result.columns]
    if missing:
        raise MarketDataError(f"OHLCV 필수 컬럼 누락: {', '.join(missing)}")
    for column in REQUIRED_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.loc[:, list(REQUIRED_COLUMNS)]


def validate_ohlcv(frame: pd.DataFrame, minimum_rows: int) -> None:
    if len(frame) < minimum_rows:
        raise MarketDataError(f"지표 계산 데이터 부족: {len(frame)} < {minimum_rows}")
    if frame[list(REQUIRED_COLUMNS)].isna().any().any():
        raise MarketDataError("OHLCV에 null 또는 숫자가 아닌 값이 있습니다")
    if (frame["high"] < frame["low"]).any():
        raise MarketDataError("High가 Low보다 작은 일봉이 있습니다")
    if (frame["close"] <= 0).any():
        raise MarketDataError("Close는 0보다 커야 합니다")
    if (frame["volume"] < 0).any():
        raise MarketDataError("Volume은 0 이상이어야 합니다")


def calculate_cci(frame: pd.DataFrame, period: int) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    mean = typical.rolling(period, min_periods=period).mean()
    mean_deviation = typical.rolling(period, min_periods=period).apply(
        lambda values: float(np.mean(np.abs(values - np.mean(values)))), raw=True
    )
    denominator = 0.015 * mean_deviation
    return ((typical - mean) / denominator).where(denominator != 0, 0.0)


def calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = average_gain / average_loss
    result = 100.0 - (100.0 / (1.0 + relative_strength))
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    result = result.mask((average_loss == 0) & (average_gain == 0), 50.0)
    return result


def calculate_atr(frame: pd.DataFrame, period: int) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = pd.Series(np.nan, index=frame.index, dtype=float)
    if len(frame) >= period:
        atr.iloc[period - 1] = true_range.iloc[:period].mean()
        for index in range(period, len(frame)):
            prior_sum = atr.iloc[index - 1] * (period - 1)
            atr.iloc[index] = (prior_sum + true_range.iloc[index]) / period
    return atr


def calculate_indicators(frame: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    result = normalize_ohlcv(frame)
    minimum_rows = max(
        max(config.indicators["cci_periods"]),
        max(config.indicators["rsi_periods"]) + 1,
        max(config.indicators["ema_periods"]),
        int(config.indicators["bollinger_window"]),
        int(config.indicators["atr_period"]),
        int(config.indicators["volume_window"]) + 1,
    )
    validate_ohlcv(result, minimum_rows)

    for period in config.indicators["cci_periods"]:
        result[f"cci{period}"] = calculate_cci(result, int(period))
    for period in config.indicators["rsi_periods"]:
        result[f"rsi{period}"] = calculate_rsi(result["close"], int(period))
    for period in config.indicators["ema_periods"]:
        result[f"ema{period}"] = (
            result["close"].ewm(span=int(period), adjust=False, min_periods=int(period)).mean()
        )

    bb_window = int(config.indicators["bollinger_window"])
    bb_stddev = float(config.indicators["bollinger_stddev"])
    center = result["close"].rolling(bb_window, min_periods=bb_window).mean()
    standard_deviation = result["close"].rolling(bb_window, min_periods=bb_window).std(ddof=0)
    result["bb_lower"] = center - bb_stddev * standard_deviation

    atr_period = int(config.indicators["atr_period"])
    result[f"atr{atr_period}"] = calculate_atr(result, atr_period)
    result["atr_pct"] = result[f"atr{atr_period}"] / result["close"]

    volume_window = int(config.indicators["volume_window"])
    volume_average = result["volume"].rolling(volume_window, min_periods=volume_window).mean()
    if config.indicators.get("volume_baseline_excludes_current_day", True):
        volume_average = volume_average.shift(1)
    result["volume_ratio"] = result["volume"] / volume_average

    candle_range = result["high"] - result["low"]
    result["close_position"] = ((result["close"] - result["low"]) / candle_range).where(
        candle_range != 0, 0.0
    )
    result["previous_close"] = result["close"].shift(1)
    return result


def snapshot_from_row(symbol: str, trade_date: pd.Timestamp, row: pd.Series) -> IndicatorSnapshot:
    required = (
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "cci5",
        "cci10",
        "rsi5",
        "rsi14",
        "ema5",
        "ema20",
        "ema60",
        "bb_lower",
        "atr14",
        "atr_pct",
        "volume_ratio",
        "close_position",
    )
    invalid = [name for name in required if name not in row or not np.isfinite(row[name])]
    if invalid:
        raise MarketDataError(f"완결되지 않은 지표: {', '.join(invalid)}")
    return IndicatorSnapshot(
        symbol=symbol.upper(),
        trade_date=pd.Timestamp(trade_date).date(),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        previous_close=Decimal(str(row["previous_close"])),
        volume=int(row["volume"]),
        cci5=float(row["cci5"]),
        cci10=float(row["cci10"]),
        rsi5=float(row["rsi5"]),
        rsi14=float(row["rsi14"]),
        ema5=float(row["ema5"]),
        ema20=float(row["ema20"]),
        ema60=float(row["ema60"]),
        bb_lower=float(row["bb_lower"]),
        atr14=float(row["atr14"]),
        atr_pct=float(row["atr_pct"]),
        volume_ratio=float(row["volume_ratio"]),
        close_position=float(row["close_position"]),
    )


def latest_snapshot(symbol: str, frame: pd.DataFrame, config: StrategyConfig) -> IndicatorSnapshot:
    indicators = calculate_indicators(frame, config)
    return snapshot_from_row(symbol, indicators.index[-1], indicators.iloc[-1])
