from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.enums import MarketRegime
from jd_holdings.core.indicators import calculate_indicators, snapshot_from_row
from jd_holdings.core.regime import evaluate_regime
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

VARIANTS = ("baseline_40", "qqq_ema60", "green_only")


def patch_stage10_core() -> None:
    original = PortfolioBacktestEngine._rebalance_core

    def staged(self, targets, quantities, prices, **kwargs):
        streaks = getattr(self, "_research_core_streaks", None)
        if streaks is None:
            streaks = {symbol: 0 for symbol in self.config.enabled_symbols}
            self._research_core_streaks = streaks
        adjusted = {}
        for symbol, target in targets.items():
            if float(target) <= 0:
                streaks[symbol] = 0
                adjusted[symbol] = 0.0
            else:
                streaks[symbol] += 1
                adjusted[symbol] = 0.10 if streaks[symbol] == 1 else float(target)
        return original(self, adjusted, quantities, prices, **kwargs)

    PortfolioBacktestEngine._rebalance_core = staged


def load_research_config():
    text = Path("strategy.yaml").read_text(encoding="utf-8")
    text = text.replace("trend_months: 10", "trend_months: 6", 1)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(text)
        config_path = handle.name
    config = load_config(config_path)
    booster_capital = Decimal("8000")
    return replace(
        config,
        global_=replace(config.global_, capital_per_symbol=booster_capital),
        portfolio=replace(config.portfolio, booster_max_weight=Decimal("0.40")),
    )


def build_regimes(variant, spy, qqq, config):
    spy_i = calculate_indicators(spy, config)
    qqq_i = calculate_indicators(qqq, config)
    common = spy_i.index.intersection(qqq_i.index)
    required = [
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
    ]
    common = common[
        spy_i.loc[common, required].notna().all(axis=1)
        & qqq_i.loc[common, required].notna().all(axis=1)
    ]
    regimes = {}
    for timestamp in common:
        s = snapshot_from_row("SPY", timestamp, spy_i.loc[timestamp])
        q = snapshot_from_row("QQQ", timestamp, qqq_i.loc[timestamp])
        regime = evaluate_regime(s, q)
        if variant == "green_only" and regime != MarketRegime.GREEN:
            regime = MarketRegime.RED
        elif variant == "qqq_ema60":
            row = qqq_i.loc[timestamp]
            if float(row["close"]) < float(row["ema60"]):
                regime = MarketRegime.RED
        regimes[timestamp] = regime
    return regimes


def run(variant: str, output: str) -> None:
    config = load_research_config()
    data_source = YFinanceDataSource("data/cache")
    end = MarketClock().latest_completed_session().isoformat()
    start = "2011-01-01"
    warmup_start = (date.fromisoformat(start) - timedelta(days=400)).isoformat()

    spy = data_source.daily("SPY", warmup_start, end)
    qqq = data_source.daily("QQQ", warmup_start, end)
    idle = data_source.daily(config.idle_cash.symbol, warmup_start, end)
    regimes = build_regimes(variant, spy, qqq, config)

    guard = config.market_regime.get("soxl_sector_guard", {})
    sector_data = {}
    if guard.get("enabled", False):
        for benchmark in guard.get("benchmark_candidates", ("SOXX", "SMH")):
            name = str(benchmark).upper()
            try:
                sector_data[name] = data_source.daily(name, warmup_start, end)
            except Exception:
                pass

    engine = StrategyBacktestEngine(config)
    booster_results = {}
    frames = {"QQQ": qqq, config.idle_cash.symbol: idle}
    for symbol in config.enabled_symbols:
        target = data_source.daily(symbol, warmup_start, end)
        frames[symbol] = target
        booster_results[symbol] = engine.run(
            symbol,
            target,
            spy,
            qqq,
            start=start,
            end=end,
            slippage=0.001,
            regimes_precomputed=regimes,
            sector_data=sector_data if symbol == "SOXL" else None,
            idle_cash_data=idle,
        )

    for underlying in config.portfolio.core_underlyings.values():
        if underlying not in frames:
            frames[underlying] = data_source.daily(underlying, warmup_start, end)

    patch_stage10_core()
    result = PortfolioBacktestEngine(config).run(
        frames, booster_results, start=start, end=end, slippage=0.001
    )
    Path(output).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def summarize(path: str) -> None:
    metrics = json.loads(Path(path).read_text(encoding="utf-8"))["metrics"]
    print(f"- Total Return: {metrics['total_return_pct']:+.2f}%")
    print(f"- CAGR: {metrics['cagr_pct']:+.2f}%")
    print(f"- MDD: {metrics['mdd_pct']:.2f}%")
    print(f"- Sharpe: {metrics['sharpe']:.3f}")
    print(f"- Sortino: {metrics['sortino']:.3f}")
    print(f"- Average Exposure: {metrics['average_exposure_pct']:.2f}%")
    print(f"- Booster Fills: {metrics['component_fills']['booster']}")
    print("| 반기 | 수익률 |")
    print("|---|---:|")
    for period, value in metrics.get("half_year_returns_pct", {}).items():
        if period >= "2022-H1":
            print(f"| {period} | {value:+.2f}% |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--output")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        run(args.variant, args.output)
