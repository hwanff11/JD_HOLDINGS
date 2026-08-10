#!/usr/bin/env python3
"""Exact MAIN-vs-FINAL JDSS comparison with calendar-year returns."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from backtest_v2_focus import BENCHMARKS, SYMBOLS, _with_entry_score, _with_stage1_guard, _with_tp, combined_metrics
from backtest_v2_remainder_exit import RemainderExitEngine

from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


def annual_returns(results, years):
    out = {}
    for year in years:
        symbol_returns = {}
        for symbol, result in results.items():
            curve = result.equity_curve
            yearly = curve[curve.index.year == year]
            if yearly.empty:
                continue
            start_equity = float(yearly.iloc[0])
            end_equity = float(yearly.iloc[-1])
            symbol_returns[symbol] = (end_equity / start_equity - 1.0) * 100.0
        out[str(year)] = {
            "combined_pct": sum(symbol_returns.values()) / len(symbol_returns) if symbol_returns else None,
            "symbols": symbol_returns,
        }
    return out


def run_pair(config, engine_cls, frames, start, end, *, wait_days=None, target_pct=None):
    results = {}
    for symbol in SYMBOLS:
        sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]} if symbol == "SOXL" else None
        engine = engine_cls(config) if wait_days is None else engine_cls(config, wait_days=wait_days, target_pct=target_pct)
        results[symbol] = engine.run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start=start,
            end=end,
            slippage=config.backtest.default_slippage,
            sector_data=sector_data,
        )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "v2_main_vs_final.json")
    args = parser.parse_args()

    main_config = load_config(ROOT / "strategy.yaml")
    final_config = _with_stage1_guard(_with_entry_score(_with_tp(main_config, "0.06"), 55))
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)).isoformat()
    frames = {symbol: source.daily(symbol, warmup_start, args.end, refresh=True) for symbol in (*SYMBOLS, *BENCHMARKS)}

    main_results = run_pair(main_config, BacktestEngine, frames, "2011-01-01", args.end)
    final_results = run_pair(
        final_config,
        RemainderExitEngine,
        frames,
        "2011-01-01",
        args.end,
        wait_days=20,
        target_pct=Decimal("0.02"),
    )
    years = range(2011, datetime.fromisoformat(args.end).year + 1)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "period": ["2011-01-01", args.end],
        "main": {
            "description": "main strategy.yaml: Entry50, TP4/8, sector guard stages 3/4",
            "combined": combined_metrics(main_results, main_config.backtest.annualization_days),
            "symbols": {s: r.metrics for s, r in main_results.items()},
            "annual_returns": annual_returns(main_results, years),
        },
        "final": {
            "description": "Entry55, TP4/6, SOXL stage1 sector guard, TP1+20d avg+2% remainder exit",
            "combined": combined_metrics(final_results, final_config.backtest.annualization_days),
            "symbols": {s: r.metrics for s, r in final_results.items()},
            "annual_returns": annual_returns(final_results, years),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# JDSS MAIN vs FINAL",
        "",
        "| Year | MAIN | FINAL | Difference |",
        "|---:|---:|---:|---:|",
    ]
    for year in years:
        key = str(year)
        m = report["main"]["annual_returns"][key]["combined_pct"]
        f = report["final"]["annual_returns"][key]["combined_pct"]
        if m is None or f is None:
            continue
        lines.append(f"| {year} | {m:+.2f}% | {f:+.2f}% | {f-m:+.2f}%p |")
    lines += ["", "## Full-period metrics", ""]
    for name in ("main", "final"):
        x = report[name]["combined"]
        lines.append(f"- {name.upper()}: CAGR {x['cagr_pct']:+.2f}%, MDD {x['mdd_pct']:.2f}%, max hold {x['max_holding_days_worst_symbol_including_open']}d, cycles {x['closed_cycles']}")
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
