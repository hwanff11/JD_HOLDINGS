#!/usr/bin/env python3
"""Optimize FINAL additional-entry price gaps with all buy scores fixed at 55."""

from __future__ import annotations

import argparse
import json
from collections import Counter
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

from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


def _candidate(base, drops):
    config = _with_stage1_guard(_with_entry_score(_with_tp(base, "0.06"), 55))
    stages = {
        stage: replace(
            config.additional_entry.stages[stage],
            min_drop_from_anchor=Decimal(str(drop)),
            min_score=55,
        )
        for stage, drop in zip((2, 3, 4), drops, strict=True)
    }
    return replace(
        config,
        additional_entry=replace(config.additional_entry, stages=stages),
    )


def _run(config, frames, start, end):
    results = {}
    for symbol in SYMBOLS:
        sector_data = (
            {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
            if symbol == "SOXL"
            else None
        )
        engine = StrategyBacktestEngine(config)
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


def _stage_counts(results):
    buy_counts = Counter()
    for result in results.values():
        for trade in result.trades:
            cycle_id = trade.get("cycle_id")
            if trade.get("side") == "BUY" and cycle_id:
                buy_counts[(result.symbol, cycle_id)] += 1
    return {
        f"stage{stage}_cycles": sum(count >= stage for count in buy_counts.values())
        for stage in (1, 2, 3, 4)
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_gap_grid.json",
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
    specs = {
        "D247_BASE": (0.02, 0.04, 0.07),
        "D246": (0.02, 0.04, 0.06),
        "D257": (0.02, 0.05, 0.07),
        "D258": (0.02, 0.05, 0.08),
        "D357": (0.03, 0.05, 0.07),
        "D358": (0.03, 0.05, 0.08),
        "D368": (0.03, 0.06, 0.08),
        "D369": (0.03, 0.06, 0.09),
    }
    segments = {
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "full_history": ("2011-01-01", args.end),
    }
    report = {"generated_at": datetime.now(UTC).isoformat(), "candidates": {}}

    for name, drops in specs.items():
        config = _candidate(base, drops)
        item = {"drops": drops, "scores": (55, 55, 55, 55), "segments": {}}
        for segment_name, (start, end) in segments.items():
            results = _run(config, frames, start, end)
            item["segments"][segment_name] = {
                "combined": combined_metrics(
                    results,
                    config.backtest.annualization_days,
                ),
                "stage_counts": _stage_counts(results),
                "symbols": {
                    symbol: result.metrics for symbol, result in results.items()
                },
            }
        report["candidates"][name] = item

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# JDSS FINAL Gap Grid (Score 55 Fixed)",
        "",
        "| Candidate | Val CAGR | MDD | P95 MAE | >40d | Max | "
        "S1/S2/S3/S4 | Full CAGR | Full MDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, item in report["candidates"].items():
        validation = item["segments"]["validation_2021_2024"]
        full = item["segments"]["full_history"]["combined"]
        metrics = validation["combined"]
        stages = validation["stage_counts"]
        stage_text = "/".join(
            str(stages[f"stage{stage}_cycles"]) for stage in (1, 2, 3, 4)
        )
        lines.append(
            f"| {name} | {metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% | "
            f"{metrics['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{metrics['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{metrics['max_holding_days_worst_symbol_including_open']}d | "
            f"{stage_text} | {full['cagr_pct']:+.2f}% | {full['mdd_pct']:.2f}% |"
        )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
