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
