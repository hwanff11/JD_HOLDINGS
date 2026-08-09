#!/usr/bin/env python3
"""Focused JDSS 2.0 swing-strategy comparison.

The goal is not a broad grid search. We change one or two dimensions at a time
from the live strategy so the effect of each change remains explainable.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import AdditionalEntryConfig, StageRule, load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


def _with_entry(base, entry_score: int):
    return replace(base, global_=replace(base.global_, entry_score=entry_score))


def _with_drops(base, drops: tuple[str, str, str]):
    stages = {
        stage: StageRule(Decimal(drop), base.additional_entry.stages[stage].min_score)
        for stage, drop in zip((2, 3, 4), drops, strict=True)
    }
    return replace(
        base,
        additional_entry=AdditionalEntryConfig(
            anchor=base.additional_entry.anchor,
            max_stage_per_day=base.additional_entry.max_stage_per_day,
            stages=stages,
        ),
    )


def _with_tp(base, tp1: str, tp2: str):
    return replace(
        base,
        take_profit=replace(
            base.take_profit,
            tp1_base=Decimal(tp1),
            tp2_base=Decimal(tp2),
        ),
    )


def build_candidates(base):
    wider = _with_drops(base, ("0.03", "0.06", "0.10"))
    return {
        "A_baseline_50_drop247_tp48": base,
        "B_entry55_only": _with_entry(base, 55),
        "C_wider_drop3610": wider,
        "D_fast_tp36": _with_tp(base, "0.03", "0.06"),
        "E_wider_drop3610_fast_tp36": _with_tp(wider, "0.03", "0.06"),
        "F_profit_runner_tp510": _with_tp(base, "0.05", "0.10"),
    }


def _weighted_metric(results: dict[str, BacktestResult], key: str) -> float:
    weighted = 0.0
    count = 0
    for result in results.values():
        cycles = int(result.metrics["closed_cycles"])
        weighted += float(result.metrics[key]) * cycles
        count += cycles
    return weighted / count if count else 0.0


def combined_metrics(results: dict[str, BacktestResult], annualization_days: int) -> dict[str, Any]:
    equity = pd.concat(
        [result.equity_curve.rename(symbol) for symbol, result in results.items()],
        axis=1,
        join="inner",
    ).sum(axis=1)
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    total_return = final / initial - 1
    cagr = (final / initial) ** (1 / years) - 1
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    closed = sum(int(result.metrics["closed_cycles"]) for result in results.values())
    signals = sum(int(result.metrics["signals"]) for result in results.values())
    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "closed_cycles": closed,
        "signals": signals,
        "avg_holding_days": round(_weighted_metric(results, "average_holding_days"), 2),
        "mae_p95_worst_symbol_pct": min(
            float(result.metrics["mae_p95_pct"]) for result in results.values()
        ),
        "worst_mae_pct": min(float(result.metrics["worst_mae_pct"]) for result in results.values()),
        "lockup_over_40_days_worst_symbol_pct": max(
            float(result.metrics["lockup_over_40_days_pct"]) for result in results.values()
        ),
        "capital_utilization_avg_pct": round(
            sum(float(result.metrics["average_capital_utilization_pct"]) for result in results.values())
            / len(results),
            2,
        ),
    }


def settings(config) -> dict[str, Any]:
    return {
        "entry_score": config.global_.entry_score,
        "minimum_reversal_score": config.global_.minimum_reversal_score,
        "stage_weights": [float(value) for value in config.position.stage_weights],
        "stage_drops": [
            float(config.additional_entry.stages[stage].min_drop_from_anchor)
            for stage in (2, 3, 4)
        ],
        "additional_scores": [config.additional_entry.stages[stage].min_score for stage in (2, 3, 4)],
        "tp": [float(config.take_profit.tp1_base), float(config.take_profit.tp2_base)],
    }


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 2.0 Focused Swing Backtest",
        "",
        "Primary decision window: **2021-2024 validation**. Recent years are reference only.",
        "",
        "| Candidate | CAGR | MDD | P95 MAE* | >40d lockup* | Cycles | Avg hold |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    rows = []
    for name, candidate in report["candidates"].items():
        m = candidate["segments"]["validation_2021_2024"]["combined"]
        rows.append((float(m["cagr_pct"]), name, m))
    for _, name, m in sorted(rows, reverse=True):
        lines.append(
            f"| {name} | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | "
            f"{m['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{m['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{m['closed_cycles']} | {m['avg_holding_days']:.1f}d |"
        )
    lines.extend(
        [
            "",
            "\* P95 MAE and >40d lockup use the worse of TQQQ/SOXL as a conservative guardrail.",
            "",
            "## Decision rule",
            "",
            "Prefer higher validation CAGR when MDD, P95 MAE and long-lockup risk stay similar. "
            "Reject a higher-return candidate if the improvement is mainly bought with materially worse tail risk "
            "or much longer capital lockup. Do not tune further from the recent-only segment.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_focused_backtest.json",
    )
    args = parser.parse_args()

    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup, args.end, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }

    segments = {
        "development_2011_2020": ("2011-01-01", "2020-12-31"),
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "recent_2025_present": ("2025-01-01", args.end),
        "full_history": ("2011-01-01", args.end),
    }
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_end": args.end,
        "slippage": float(base.backtest.default_slippage),
        "candidates": {},
    }

    for name, config in build_candidates(base).items():
        candidate: dict[str, Any] = {"settings": settings(config), "segments": {}}
        for segment_name, (start, end) in segments.items():
            results = {
                symbol: BacktestEngine(config).run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=base.backtest.default_slippage,
                    sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None,
                )
                for symbol in SYMBOLS
            }
            candidate["segments"][segment_name] = {
                "combined": combined_metrics(results, config.backtest.annualization_days),
                "symbols": {symbol: result.metrics for symbol, result in results.items()},
            }
        report["candidates"][name] = candidate

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = args.output.with_suffix(".md")
    summary_path.write_text(markdown_summary(report), encoding="utf-8")
    print(summary_path.read_text(encoding="utf-8"))
    print(f"saved_json={args.output}")
    print(f"saved_summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
