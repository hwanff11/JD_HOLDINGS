from __future__ import annotations

import argparse
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource


def patch_core(variant: str) -> None:
    original = PortfolioBacktestEngine._rebalance_core

    def staged(self, targets, quantities, prices, **kwargs):
        state = getattr(self, "_research_expand_state", None)
        if state is None:
            state = {symbol: {"active": False, "reference": 0.0} for symbol in self.config.enabled_symbols}
            self._research_expand_state = state
        adjusted = {}
        threshold = 0.0 if variant == "confirm_0" else 0.02
        for symbol, target in targets.items():
            target = float(target)
            item = state[symbol]
            if target <= 0:
                item["active"] = False
                item["reference"] = 0.0
                adjusted[symbol] = 0.0
                continue
            price = float(prices[symbol])
            if not item["active"]:
                item["active"] = True
                item["reference"] = price
                adjusted[symbol] = 0.10
            elif variant == "stage_10":
                adjusted[symbol] = target
            else:
                confirmed = price >= item["reference"] * (1.0 + threshold)
                adjusted[symbol] = target if confirmed else 0.10
        return original(self, adjusted, quantities, prices, **kwargs)

    PortfolioBacktestEngine._rebalance_core = staged


def run(variant: str, output: str) -> None:
    text = Path("strategy.yaml").read_text(encoding="utf-8").replace(
        "trend_months: 10", "trend_months: 6", 1
    )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        config_path = handle.name
    config = load_config(config_path)
    data_source = YFinanceDataSource("data/cache")
    end = MarketClock().latest_completed_session().isoformat()
    start = "2011-01-01"
    warmup_start = (date.fromisoformat(start) - timedelta(days=400)).isoformat()
    spy = data_source.daily("SPY", warmup_start, end)
    qqq = data_source.daily("QQQ", warmup_start, end)
    idle = data_source.daily(config.idle_cash.symbol, warmup_start, end)
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
    completed_results = {}
    target_frames = {}
    for symbol in config.enabled_symbols:
        target = data_source.daily(symbol, warmup_start, end)
        target_frames[symbol] = target
        completed_results[symbol] = engine.run(
            symbol, target, spy, qqq, start=start, end=end, slippage=0.001,
            sector_data=sector_data if symbol == "SOXL" else None, idle_cash_data=idle,
        )
    raw_frames = {**target_frames, "QQQ": qqq, config.idle_cash.symbol: idle}
    for underlying in config.portfolio.core_underlyings.values():
        if underlying not in raw_frames:
            raw_frames[underlying] = data_source.daily(underlying, warmup_start, end)
    patch_core(variant)
    result = PortfolioBacktestEngine(config).run(
        raw_frames, completed_results, start=start, end=end, slippage=0.001
    )
    Path(output).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(path: str) -> None:
    metrics = json.loads(Path(path).read_text(encoding="utf-8"))["metrics"]
    print(f"- Total Return: {metrics['total_return_pct']:+.2f}%")
    print(f"- CAGR: {metrics['cagr_pct']:+.2f}%")
    print(f"- MDD: {metrics['mdd_pct']:.2f}%")
    print(f"- Sharpe: {metrics['sharpe']:.3f}")
    print(f"- Average Exposure: {metrics['average_exposure_pct']:.2f}%")
    print("| 반기 | 수익률 |")
    print("|---|---:|")
    for period, value in metrics.get("half_year_returns_pct", {}).items():
        if period >= "2022-H1":
            print(f"| {period} | {value:+.2f}% |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["stage_10", "confirm_0", "confirm_2"])
    parser.add_argument("--output")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    summarize(args.summarize) if args.summarize else run(args.variant, args.output)
