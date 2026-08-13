#!/usr/bin/env python3
"""Research-only ablation of the five oversold subcomponents in JDSS V3.1.1."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("TQQQ", "SOXL")
DATA = ("TQQQ", "SOXL", "SPY", "QQQ", "SOXX", "SMH")
SEGMENTS = {
    "full_2011": ("2011-01-01", "2026-08-12"),
    "recent_2022": ("2022-01-01", "2026-08-12"),
    "oos_2023": ("2023-01-01", "2026-08-12"),
}


def zero_bands(bands):
    return [[threshold, 0] for threshold, _ in bands]


def variants(base):
    result = {"baseline": base}
    for key in ("cci5", "cci10", "rsi5", "rsi14"):
        scoring = dict(base.scoring)
        scoring[key] = {**scoring[key], "bands": zero_bands(scoring[key]["bands"])}
        result[f"drop_{key}"] = replace(base, scoring=scoring)
    scoring = dict(base.scoring)
    scoring["bollinger"] = {**scoring["bollinger"], "deep_score": 0, "touch_score": 0}
    result["drop_bollinger"] = replace(base, scoring=scoring)
    return result


def main():
    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = "2009-11-27"
    frames = {s: source.daily(s, warmup, "2026-08-12", refresh=False) for s in DATA}
    report = {"method": "one-at-a-time oversold subcomponent ablation; production floor/gates unchanged", "segments": {}}
    for seg, (start, end) in SEGMENTS.items():
        report["segments"][seg] = {}
        for name, cfg in variants(base).items():
            engine = BacktestEngine(cfg)
            boosters = {}
            for symbol in SYMBOLS:
                boosters[symbol] = engine.run(
                    symbol, frames[symbol], frames["SPY"], frames["QQQ"],
                    start=start, end=end, slippage=0.001,
                    sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]} if symbol == "SOXL" else None,
                )
            portfolio = PortfolioBacktestEngine(cfg).run(frames, boosters, start=start, end=end, slippage=0.001)
            m = portfolio.metrics
            report["segments"][seg][name] = {
                "total_return_pct": m.get("total_return_pct"), "cagr_pct": m.get("cagr_pct"),
                "mdd_pct": m.get("mdd_pct"), "sharpe": m.get("sharpe"), "sortino": m.get("sortino"),
                "average_exposure_pct": m.get("average_exposure_pct"),
                "core_fills": m.get("core_trade_fills"), "booster_fills": m.get("booster_trade_fills"),
                "max_invested_cost": m.get("max_invested_cost"),
                "booster_signals": sum(int(r.metrics.get("signals", 0)) for r in boosters.values()),
                "booster_entries": sum(int(r.metrics.get("executed_entries", 0)) for r in boosters.values()),
                "closed_cycles": sum(int(r.metrics.get("closed_cycles", 0)) for r in boosters.values()),
            }
            print(seg, name, report["segments"][seg][name])
    (ROOT / "oversold_ablation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
