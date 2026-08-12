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
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

VARIANTS = ("tp2_6", "tp2_8", "tp2_10")
TP2_RATES = {
    "tp2_6": Decimal("0.06"),
    "tp2_8": Decimal("0.08"),
    "tp2_10": Decimal("0.10"),
}


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


def load_research_config(variant: str):
    text = Path("strategy.yaml").read_text(encoding="utf-8")
    text = text.replace("trend_months: 10", "trend_months: 6", 1)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(text)
        config_path = handle.name
    config = load_config(config_path)

    # Research baseline established in prior experiments:
    # 6-month core trend, first active month 10% then 15%, 40% booster.
    # Keep production validation unchanged and expand research exposure in memory only.
    return replace(
        config,
        global_=replace(config.global_, capital_per_symbol=Decimal("8000")),
        portfolio=replace(config.portfolio, booster_max_weight=Decimal("0.40")),
        take_profit=replace(
            config.take_profit,
            tp1_base=Decimal("0.04"),
            tp2_base=TP2_RATES[variant],
            use_atr=False,
        ),
    )


def run(variant: str, output: str) -> None:
    config = load_research_config(variant)
    data_source = YFinanceDataSource("data/cache")
    market_clock = MarketClock()
    start = "2011-01-01"
    end = market_clock.latest_completed_session().isoformat()
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
    booster_results = {}
    target_frames = {}
    for symbol in config.enabled_symbols:
        target = data_source.daily(symbol, warmup_start, end)
        target_frames[symbol] = target
        booster_results[symbol] = engine.run(
            symbol,
            target,
            spy,
            qqq,
            start=start,
            end=end,
            slippage=0.001,
            sector_data=sector_data if symbol == "SOXL" else None,
            idle_cash_data=idle,
        )

    frames = {**target_frames, "QQQ": qqq, config.idle_cash.symbol: idle}
    for underlying in config.portfolio.core_underlyings.values():
        if underlying not in frames:
            frames[underlying] = data_source.daily(underlying, warmup_start, end)

    patch_stage10_core()
    result = PortfolioBacktestEngine(config).run(
        frames,
        booster_results,
        start=start,
        end=end,
        slippage=0.001,
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
    print(f"- Idle Cash Income: ${metrics['idle_cash_income']:.2f}")
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
