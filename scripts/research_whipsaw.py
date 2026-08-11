from __future__ import annotations

import argparse
import json
import tempfile
from datetime import timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource


def patch_engine(variant: str) -> None:
    if variant == "baseline_6m":
        return

    def filtered(cls, frame, index, months):
        history = frame.loc[: index[-1], "close"].dropna()
        monthly = history.groupby(history.index.to_period("M")).last()
        ma6 = monthly.rolling(6, min_periods=6).mean()
        active = monthly > ma6
        if variant == "confirm_10m":
            active &= monthly > monthly.rolling(10, min_periods=10).mean()
        elif variant == "slope_3m":
            active &= ma6 > ma6.shift(3)
        result = pd.Series(False, index=index)
        for ts in cls._month_end_sessions(index):
            result.loc[ts] = bool(active.get(ts.to_period("M"), False))
        return result

    PortfolioBacktestEngine._monthly_trend = classmethod(filtered)


def run(variant: str, output: str) -> None:
    text = Path("strategy.yaml").read_text(encoding="utf-8").replace(
        "trend_months: 10", "trend_months: 6", 1
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(text)
        config_path = handle.name

    config = load_config(config_path)
    data_source = YFinanceDataSource("data/cache")
    market_clock = MarketClock()
    end = market_clock.latest_completed_session()
    start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = (start - timedelta(days=400)).isoformat()
    end_text = end.isoformat()

    spy = data_source.daily("SPY", warmup_start, end_text)
    qqq = data_source.daily("QQQ", warmup_start, end_text)
    idle_cash = data_source.daily(config.idle_cash.symbol, warmup_start, end_text)

    raw_frames = {
        "SPY": spy,
        "QQQ": qqq,
        config.idle_cash.symbol: idle_cash,
    }
    for underlying in config.portfolio.core_underlyings.values():
        if underlying not in raw_frames:
            raw_frames[underlying] = data_source.daily(
                underlying, warmup_start, end_text
            )

    sector_data = {}
    for benchmark in ("SOXX", "SMH"):
        try:
            sector_data[benchmark] = data_source.daily(
                benchmark, warmup_start, end_text
            )
        except Exception:
            pass

    strategy_engine = StrategyBacktestEngine(config)
    booster_results = {}
    for symbol in config.enabled_symbols:
        target = data_source.daily(symbol, warmup_start, end_text)
        raw_frames[symbol] = target
        booster_results[symbol] = strategy_engine.run(
            symbol,
            target,
            spy,
            qqq,
            start=start,
            end=end,
            slippage=0.001,
            sector_data=sector_data if symbol == "SOXL" else None,
            idle_cash_data=idle_cash,
        )

    patch_engine(variant)
    result = PortfolioBacktestEngine(config).run(
        raw_frames,
        booster_results,
        start=start,
        end=end,
        slippage=0.001,
    )
    Path(output).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def summary(path: str) -> None:
    metrics = json.loads(Path(path).read_text(encoding="utf-8"))["metrics"]
    print(f"## {Path(path).stem}")
    print(f"- Total Return: {metrics['total_return_pct']:+.2f}%")
    print(f"- CAGR: {metrics['cagr_pct']:+.2f}%")
    print(f"- MDD: {metrics['mdd_pct']:.2f}%")
    print(f"- Sharpe: {metrics['sharpe']:.3f}")
    print("| 반기 | 수익률 |")
    print("|---|---:|")
    for period, value in metrics.get("half_year_returns_pct", {}).items():
        if period >= "2022-H1":
            print(f"| {period} | {value:+.2f}% |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant")
    parser.add_argument("--output")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        summary(args.summarize)
    else:
        run(args.variant, args.output)
