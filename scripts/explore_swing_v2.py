#!/usr/bin/env python3
"""Evaluate a small set of high-turnover swing configurations."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from compare_v131 import _combined_metrics

from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.config import AdditionalEntryConfig, StageRule, load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


def _candidate(
    base,
    *,
    entry: int,
    chase: str,
    weights: tuple[str, str, str, str],
    drops: tuple[str, str, str],
    add_scores: tuple[int, int, int],
    tp: tuple[str, str],
):
    decimal_weights = tuple(Decimal(value) for value in weights)
    cumulative = tuple(sum(decimal_weights[:index]) for index in range(1, 5))
    return replace(
        base,
        global_=replace(
            base.global_, entry_score=entry, entry_max_chase_pct=Decimal(chase)
        ),
        position=replace(
            base.position,
            stage_weights=decimal_weights,
            cumulative_weights=cumulative,
        ),
        additional_entry=AdditionalEntryConfig(
            anchor=base.additional_entry.anchor,
            max_stage_per_day=1,
            stages={
                stage: StageRule(Decimal(drop), score)
                for stage, drop, score in zip((2, 3, 4), drops, add_scores, strict=True)
            },
        ),
        take_profit=replace(
            base.take_profit,
            tp1_base=Decimal(tp[0]),
            tp2_base=Decimal(tp[1]),
        ),
    )


def candidates(base):
    values = {
        "v1.3.2_baseline": base,
        "swing_a": _candidate(
            base,
            entry=68,
            chase="0.03",
            weights=("0.40", "0.30", "0.20", "0.10"),
            drops=("0.03", "0.06", "0.10"),
            add_scores=(72, 74, 76),
            tp=("0.04", "0.08"),
        ),
        "swing_b": _candidate(
            base,
            entry=64,
            chase="0.04",
            weights=("0.40", "0.30", "0.20", "0.10"),
            drops=("0.02", "0.04", "0.07"),
            add_scores=(68, 70, 72),
            tp=("0.03", "0.06"),
        ),
        "swing_c": _candidate(
            base,
            entry=66,
            chase="0.03",
            weights=("0.40", "0.30", "0.20", "0.10"),
            drops=("0.025", "0.05", "0.09"),
            add_scores=(70, 72, 74),
            tp=("0.04", "0.07"),
        ),
        "swing_d": _candidate(
            base,
            entry=68,
            chase="0.03",
            weights=("0.40", "0.30", "0.20", "0.10"),
            drops=("0.03", "0.06", "0.10"),
            add_scores=(72, 74, 76),
            tp=("0.03", "0.06"),
        ),
        "swing_e": _candidate(
            base,
            entry=64,
            chase="0.04",
            weights=("0.40", "0.30", "0.20", "0.10"),
            drops=("0.02", "0.04", "0.07"),
            add_scores=(68, 70, 72),
            tp=("0.04", "0.08"),
        ),
        "swing_f_entry62": _candidate(
            base,
            entry=62,
            chase="0.04",
            weights=("0.40", "0.30", "0.20", "0.10"),
            drops=("0.02", "0.04", "0.07"),
            add_scores=(66, 68, 70),
            tp=("0.03", "0.06"),
        ),
        "swing_g_entry60": _candidate(
            base,
            entry=60,
            chase="0.04",
            weights=("0.40", "0.30", "0.20", "0.10"),
            drops=("0.02", "0.04", "0.07"),
            add_scores=(64, 66, 68),
            tp=("0.03", "0.06"),
        ),
    }
    return values


def _settings(config):
    return {
        "entry_score": config.global_.entry_score,
        "minimum_reversal_score": config.global_.minimum_reversal_score,
        "entry_max_chase_pct": float(config.global_.entry_max_chase_pct),
        "stage_weights": [float(value) for value in config.position.stage_weights],
        "stage_drops": [
            float(config.additional_entry.stages[stage].min_drop_from_anchor)
            for stage in (2, 3, 4)
        ],
        "additional_entry_scores": [
            config.additional_entry.stages[stage].min_score for stage in (2, 3, 4)
        ],
        "take_profit": [
            float(config.take_profit.tp1_base),
            float(config.take_profit.tp2_base),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-04")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    segments = {
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "recent_2025": ("2025-01-01", "2025-12-31"),
        "recent_2026_ytd": ("2026-01-01", "2026-07-31"),
        "full_history": ("2011-01-01", args.end),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "slippage": 0.001,
        "candidates": {},
    }
    for name, config in candidates(base).items():
        candidate = {"settings": _settings(config), "segments": {}}
        for segment, (start, end) in segments.items():
            results = {
                symbol: BacktestEngine(config).run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=0.001,
                    sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None,
                )
                for symbol in SYMBOLS
            }
            candidate["segments"][segment] = {
                "combined": _combined_metrics(results, config.backtest.annualization_days),
                "symbols": {
                    symbol: {
                        "metrics": result.metrics,
                        "open_position": result.open_position,
                    }
                    for symbol, result in results.items()
                },
            }
            metrics = candidate["segments"][segment]["combined"]
            print(
                f"{segment} {name}: return={metrics['total_return_pct']:+.2f}% "
                f"MDD={metrics['mdd_pct']:.2f}% cycles={metrics['closed_cycles']}"
            )
        report["candidates"][name] = candidate
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
