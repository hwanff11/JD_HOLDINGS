#!/usr/bin/env python3
"""Isolate FINAL entry/additional-entry score thresholds."""

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


def config_for(base, entry, scores):
    config = _with_stage1_guard(_with_entry_score(_with_tp(base, "0.06"), entry))
    stages = {
        stage: replace(config.additional_entry.stages[stage], min_score=score)
        for stage, score in zip((2, 3, 4), scores, strict=True)
    }
    return replace(
        config,
        additional_entry=replace(config.additional_entry, stages=stages),
    )


def run(config, frames, start, end):
    results = {}
    for symbol in SYMBOLS:
        sector_data = (
            {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
            if symbol == "SOXL"
            else None
        )
        engine = RemainderExitEngine(
            config,
            wait_days=20,
            target_pct=Decimal("0.02"),
        )
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
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_score_grid.json",
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
        "E55_S525456": (55, (52, 54, 56)),
        "E55_S555555": (55, (55, 55, 55)),
        "E55_S555759": (55, (55, 57, 59)),
        "E55_S556065": (55, (55, 60, 65)),
        "E50_S525456": (50, (52, 54, 56)),
        "E50_S505254": (50, (50, 52, 54)),
    }
    segments = {
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "full_history": ("2011-01-01", args.end),
    }
    report = {"generated_at": datetime.now(UTC).isoformat(), "candidates": {}}

    for name, (entry, scores) in specs.items():
        config = config_for(base, entry, scores)
        item = {"entry": entry, "scores": scores, "segments": {}}
        for segment_name, (start, end) in segments.items():
            results = run(config, frames, start, end)
            item["segments"][segment_name] = {
                "combined": combined_metrics(
                    results,
                    config.backtest.annualization_days,
                ),
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
        "# JDSS FINAL Score Grid",
        "",
        "| Candidate | Val CAGR | MDD | P95 MAE | >40d | Cycles | "
        "Full CAGR | Full MDD | Full Cycles |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, item in report["candidates"].items():
        validation = item["segments"]["validation_2021_2024"]["combined"]
        full = item["segments"]["full_history"]["combined"]
        lines.append(
            f"| {name} | {validation['cagr_pct']:+.2f}% | "
            f"{validation['mdd_pct']:.2f}% | "
            f"{validation['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{validation['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{validation['closed_cycles']} | {full['cagr_pct']:+.2f}% | "
            f"{full['mdd_pct']:.2f}% | {full['closed_cycles']} |"
        )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
