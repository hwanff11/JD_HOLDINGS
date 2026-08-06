from __future__ import annotations

import numpy as np
import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine


def make_market_frame(closes: np.ndarray, *, bullish_candles: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=len(closes))
    opens = closes - 0.5 if bullish_candles else closes - 0.1
    highs = closes * (1.01 if bullish_candles else 1.03)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": closes * 0.98,
            "close": closes,
            "volume": np.full(len(closes), 1_000_000.0),
        },
        index=dates,
    )


def test_backtest_executes_signal_on_later_session(config):
    length = 220
    benchmark = np.linspace(100, 150, length)
    target = np.full(length, 100.0)
    target[140:151] = np.linspace(99, 70, 11)
    target[151:170] = np.linspace(70, 115, 19)
    target[170:] = np.linspace(115, 130, length - 170)
    target_frame = make_market_frame(target, bullish_candles=True)
    target_frame.loc[target_frame.index[140:152], "volume"] = 2_500_000
    result = BacktestEngine(config).run(
        "TQQQ",
        target_frame,
        make_market_frame(benchmark),
        make_market_frame(benchmark * 1.1),
        slippage=0,
    )
    buy_trades = [trade for trade in result.trades if trade["side"] == "BUY"]
    assert result.signals
    assert buy_trades
    assert buy_trades[0]["date"] > result.signals[0]["trade_date"]
    assert result.metrics["mdd_pct"] <= 0
    assert result.metrics["tp1_reach_rate_pct"] <= 100
    assert result.metrics["tp2_reach_rate_pct"] <= 100
    assert "median_mae_pct" in result.metrics
    assert "mae_p90_pct" in result.metrics
    assert "mae_p95_pct" in result.metrics
    assert result.metrics["sector_guard_requested"] == 0
    assert result.metrics["sector_guard_applied"] == 0


def test_soxl_backtest_reports_sector_guard_application(config):
    length = 220
    benchmark = np.linspace(100, 150, length)
    target = np.linspace(80, 110, length)
    sector = np.linspace(90, 130, length)
    result = BacktestEngine(config).run(
        "SOXL",
        make_market_frame(target, bullish_candles=True),
        make_market_frame(benchmark),
        make_market_frame(benchmark * 1.1),
        slippage=0,
        sector_data={
            "SOXX": make_market_frame(sector),
            "SMH": make_market_frame(sector * 1.05),
        },
    )
    assert result.metrics["sector_guard_requested"] == 1
    assert result.metrics["sector_guard_applied"] == 1
    assert result.metrics["sector_guard_blocks"] >= 0


def test_soxl_backtest_marks_missing_sector_data(config):
    length = 220
    benchmark = np.linspace(100, 150, length)
    target = np.linspace(80, 110, length)
    result = BacktestEngine(config).run(
        "SOXL",
        make_market_frame(target, bullish_candles=True),
        make_market_frame(benchmark),
        make_market_frame(benchmark * 1.1),
        slippage=0,
    )
    assert result.metrics["sector_guard_requested"] == 1
    assert result.metrics["sector_guard_applied"] == 0
