from __future__ import annotations

import numpy as np
import pandas as pd

from jd_holdings.core.indicators import calculate_indicators, snapshot_from_row


def test_volume_ratio_uses_previous_20_days(config):
    dates = pd.bdate_range("2026-01-01", periods=100)
    close = np.linspace(100, 120, len(dates))
    volume = np.full(len(dates), 100.0)
    volume[-1] = 200.0
    frame = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )
    result = calculate_indicators(frame, config)
    assert result.iloc[-1]["volume_ratio"] == 2.0
    assert result.iloc[-1]["ema60"] > 0


def test_zero_range_candle_has_zero_close_position(config):
    dates = pd.bdate_range("2026-01-01", periods=100)
    close = np.linspace(100, 120, len(dates))
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000,
        },
        index=dates,
    )
    frame.loc[dates[-1], ["open", "high", "low", "close"]] = 120
    result = calculate_indicators(frame, config)
    assert result.iloc[-1]["close_position"] == 0.0
    snapshot = snapshot_from_row("TQQQ", result.index[-1], result.iloc[-1])
    assert snapshot.close_position == 0.0
