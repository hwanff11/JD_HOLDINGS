#!/usr/bin/env python3
"""Final execution-cost stress test for JDSS 2.0 finalists."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from backtest_v2_focus import (
    BENCHMARKS,
    SYMBOLS,
    _with_entry_score,
    _with_stage1_guard,
    _with_tp,
    combined_metrics,
)
from backtest_v2_remainder_exit import RemainderExitEngine

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


def _finalist(base, tp2: str, wait_days: int, target: str):
    config = _with_stage1_guard(_with_entry_score(_with_tp(base, tp2), 55))
    return config, wait_days, Decimal(target)


def _with_fees(config, fee: str):
    value = Decimal(fee)
    return replace(config, global_=replace(config.global_, buy_fee=value, sell_fee=value))


def scenarios():
    return {
        "BASE": ("0.001", "0.001"),
        "SLIP_020": ("0.001", "0.002"),
        "SLIP_030": ("0.001", "0.003"),
        "FEE_020_SLIP_020": ("0.002", "0.002"),
        "FEE_030_SLIP_030": ("0.003", "0.003"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "v2_final_stress.json"
    )
    args = parser.parse_args()
    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)
    ).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup_start, args.end, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    finalists = {
        "TP46_CHAMPION": _finalist(base, "0.06", 20, "0.02"),
        "TP48_COMPARE": _finalist(base, "0.08", 40, "0.00"),
    }
    segments = {
        "development_2011_2020": ("2011-01-01", "2020-12-31"),
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "recent_2025_present": ("2025-01-01", args.end),
        "full_history": ("2011-01-01", args.end),
    }
    report = {"generated_at": datetime.now(UTC).isoformat(), "finalists": {}}
    for finalist_name, (raw_config, wait_days, target_pct) in finalists.items():
        finalist = {"scenarios": {}}
        for scenario_name, (fee, slippage) in scenarios().items():
            config = _with_fees(raw_config, fee)
            scenario = {"fee": float(fee), "slippage": float(slippage), "segments": {}}
            for segment_name, (start, end) in segments.items():
                results = {}
                for symbol in SYMBOLS:
                    sector_data = (
                        {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                        if symbol == "SOXL"
                        else None
                    )
                    engine = RemainderExitEngine(
                        config, wait_days=wait_days, target_pct=target_pct
                    )
                    results[symbol] = engine.run(
                        symbol,
                        frames[symbol],
                        frames["SPY"],
                        frames["QQQ"],
                        start=start,
                        end=end,
                        slippage=Decimal(slippage),
                        sector_data=sector_data,
                    )
                scenario["segments"][segment_name] = {
                    "combined": combined_metrics(results, config.backtest.annualization_days),
                    "symbols": {symbol: result.metrics for symbol, result in results.items()},
                }
            finalist["scenarios"][scenario_name] = scenario
        report["finalists"][finalist_name] = finalist
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# JDSS 2.0 Final Execution Stress Test",
        "",
        "| Finalist | Scenario | Val CAGR | Val MDD | Recent CAGR | Full CAGR | Max hold |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for finalist_name, finalist in report["finalists"].items():
        for scenario_name, scenario in finalist["scenarios"].items():
            val = scenario["segments"]["validation_2021_2024"]["combined"]
            recent = scenario["segments"]["recent_2025_present"]["combined"]
            full = scenario["segments"]["full_history"]["combined"]
            lines.append(
                f"| {finalist_name} | {scenario_name} | {val['cagr_pct']:+.2f}% | "
                f"{val['mdd_pct']:.2f}% | {recent['cagr_pct']:+.2f}% | "
                f"{full['cagr_pct']:+.2f}% | "
                f"{val['max_holding_days_worst_symbol_including_open']}d |"
            )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
