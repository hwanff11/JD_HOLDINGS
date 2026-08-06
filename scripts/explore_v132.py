#!/usr/bin/env python3
"""Small, pre-declared v1.3.2 candidate ablation."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from compare_v131 import _combined_metrics

from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


def candidates(base):
    current = base.scoring["calibration"]

    def candidate(name, *, reversal=None, entry=None, exponents=None):
        config = base
        if reversal is not None or entry is not None:
            config = replace(
                config,
                global_=replace(
                    config.global_,
                    minimum_reversal_score=(
                        reversal if reversal is not None else config.global_.minimum_reversal_score
                    ),
                    entry_score=entry if entry is not None else config.global_.entry_score,
                ),
            )
        if exponents is not None:
            config = replace(
                config,
                scoring={
                    **config.scoring,
                    "calibration": {**current, "enabled": True, "exponents": exponents},
                },
            )
        return name, config

    moderate = {"regime": 1.0, "oversold": 0.45, "reversal": 0.55, "volume": 0.65, "atr": 0.9}
    return dict(
        [
            candidate("v1.3.1_baseline"),
            candidate("gate_off_current_cal", reversal=0),
            candidate("gate5_moderate_cal", exponents=moderate),
            candidate("gate0_moderate_cal", reversal=0, exponents=moderate),
            candidate("gate5_entry72", entry=72),
            candidate("gate0_entry72", reversal=0, entry=72),
            candidate("gate5_moderate_entry72", entry=72, exponents=moderate),
        ]
    )


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
        "full_history": ("2011-01-01", args.end),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "slippage": 0.001,
        "candidates": {},
    }
    for name, config in candidates(base).items():
        report["candidates"][name] = {
            "minimum_reversal_score": config.global_.minimum_reversal_score,
            "entry_score": config.global_.entry_score,
            "exponents": config.scoring["calibration"]["exponents"],
            "segments": {},
        }
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
            report["candidates"][name]["segments"][segment] = {
                "combined": _combined_metrics(results, config.backtest.annualization_days),
                "symbols": {symbol: result.metrics for symbol, result in results.items()},
            }
            m = report["candidates"][name]["segments"][segment]["combined"]
            print(
                f"{segment} {name}: return={m['total_return_pct']:+.2f}% "
                f"CAGR={m['cagr_pct']:+.2f}% MDD={m['mdd_pct']:.2f}% "
                f"cycles={m['closed_cycles']}"
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
