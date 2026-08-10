#!/usr/bin/env python3
"""Optimize FINAL position weights, averaging gaps, and stage scores."""

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


def _decimals(values):
    return tuple(Decimal(str(value)) for value in values)


def _position(config, weights, drops, scores):
    weight_values = _decimals(weights)
    cumulative = []
    total = Decimal("0")
    for weight in weight_values:
        total += weight
        cumulative.append(total)
    stages = {
        stage: replace(
            config.additional_entry.stages[stage],
            min_drop_from_anchor=Decimal(str(drop)),
            min_score=int(score),
        )
        for stage, drop, score in zip((2, 3, 4), drops, scores, strict=True)
    }
    return replace(
        config,
        position=replace(
            config.position,
            stage_weights=weight_values,
            cumulative_weights=tuple(cumulative),
        ),
        additional_entry=replace(config.additional_entry, stages=stages),
    )


def _final_root(base):
    return _with_stage1_guard(_with_entry_score(_with_tp(base, "0.06"), 55))


def candidates(base):
    root = _final_root(base)
    specs = {
        "FINAL_BASE": ((0.40, 0.30, 0.20, 0.10), (0.02, 0.04, 0.07), (52, 54, 56)),
        "W30302515": ((0.30, 0.30, 0.25, 0.15), (0.02, 0.04, 0.07), (52, 54, 56)),
        "W25252525": ((0.25, 0.25, 0.25, 0.25), (0.02, 0.04, 0.07), (52, 54, 56)),
        "W25302520": ((0.25, 0.30, 0.25, 0.20), (0.02, 0.04, 0.07), (52, 54, 56)),
        "W20252530": ((0.20, 0.25, 0.25, 0.30), (0.02, 0.04, 0.07), (52, 54, 56)),
        "D358": ((0.40, 0.30, 0.20, 0.10), (0.03, 0.05, 0.08), (52, 54, 56)),
        "D369": ((0.40, 0.30, 0.20, 0.10), (0.03, 0.06, 0.09), (52, 54, 56)),
        "W30_D358": ((0.30, 0.30, 0.25, 0.15), (0.03, 0.05, 0.08), (52, 54, 56)),
        "W30_D369": ((0.30, 0.30, 0.25, 0.15), (0.03, 0.06, 0.09), (52, 54, 56)),
        "W25_D369": ((0.25, 0.25, 0.25, 0.25), (0.03, 0.06, 0.09), (52, 54, 56)),
        "W30_D369_S555759": (
            (0.30, 0.30, 0.25, 0.15),
            (0.03, 0.06, 0.09),
            (55, 57, 59),
        ),
        "W30_D369_S556065": (
            (0.30, 0.30, 0.25, 0.15),
            (0.03, 0.06, 0.09),
            (55, 60, 65),
        ),
        "W25302520_D369_S555759": (
            (0.25, 0.30, 0.25, 0.20),
            (0.03, 0.06, 0.09),
            (55, 57, 59),
        ),
    }
    return {
        name: (_position(root, weights, drops, scores), weights, drops, scores)
        for name, (weights, drops, scores) in specs.items()
    }


def _run(config, frames, start, end):
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
        default=ROOT / "reports" / "v2_position_opt.json",
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
    segments = {
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "full_history": ("2011-01-01", args.end),
    }
    report = {"generated_at": datetime.now(UTC).isoformat(), "candidates": {}}
    for name, (config, weights, drops, scores) in candidates(base).items():
        item = {
            "settings": {
                "weights": list(weights),
                "drops": list(drops),
                "scores": list(scores),
            },
            "segments": {},
        }
        for segment_name, (start, end) in segments.items():
            results = _run(config, frames, start, end)
            item["segments"][segment_name] = {
                "combined": combined_metrics(results, config.backtest.annualization_days),
                "symbols": {symbol: result.metrics for symbol, result in results.items()},
            }
        report["candidates"][name] = item

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# JDSS FINAL Position Optimization",
        "",
        "| Candidate | Val CAGR | Val MDD | Val P95 MAE | >40d | Val max | Full CAGR | Full MDD | Full max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, item in report["candidates"].items():
        val = item["segments"]["validation_2021_2024"]["combined"]
        full = item["segments"]["full_history"]["combined"]
        lines.append(
            f"| {name} | {val['cagr_pct']:+.2f}% | {val['mdd_pct']:.2f}% | "
            f"{val['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{val['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{val['max_holding_days_worst_symbol_including_open']}d | "
            f"{full['cagr_pct']:+.2f}% | {full['mdd_pct']:.2f}% | "
            f"{full['max_holding_days_worst_symbol_including_open']}d |"
        )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
