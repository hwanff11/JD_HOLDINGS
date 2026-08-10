#!/usr/bin/env python3
"""Focused JDSS 2.0 phase-4 sector-guard comparison.

The audit found a severe SOXL stale cycle that started near the 2021 peak and
remained open after TP1. Phase 4 keeps the core entry/add rules fixed and tests
whether extending the existing semiconductor EMA60 guard to SOXL stage 1 can
avoid this failure mode without sacrificing too much return.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


def _with_tp(base, tp1: str, tp2: str):
    return replace(
        base,
        take_profit=replace(
            base.take_profit,
            tp1_base=Decimal(tp1),
            tp2_base=Decimal(tp2),
        ),
    )


def _with_soxl_stage1_guard(base, rule: str):
    guard = dict(base.market_regime.get("soxl_sector_guard", {}))
    blocked = {int(stage) for stage in guard.get("blocked_stages", (3, 4))}
    blocked.add(1)
    guard["blocked_stages"] = sorted(blocked)
    guard["rule"] = rule
    return replace(
        base,
        market_regime={**base.market_regime, "soxl_sector_guard": guard},
    )


def build_candidates(base):
    tp46 = _with_tp(base, "0.04", "0.06")
    return {
        "A_baseline_tp48_guard34": base,
        "I_tp46_guard34": tp46,
        "V_tp48_guard134_any": _with_soxl_stage1_guard(
            base, "any_benchmark_below_ema60"
        ),
        "W_tp46_guard134_any": _with_soxl_stage1_guard(
            tp46, "any_benchmark_below_ema60"
        ),
        "X_tp46_guard134_all": _with_soxl_stage1_guard(
            tp46, "all_benchmarks_below_ema60"
        ),
    }


def _holding_days_including_open(result: BacktestResult) -> list[int]:
    days = [int(cycle["holding_days"]) for cycle in result.closed_cycles]
    if int(result.open_position["quantity"]) > 0:
        days.append(int(result.open_position["holding_days"]))
    return days


def _average_holding_including_open(results: dict[str, BacktestResult]) -> float:
    days = [day for result in results.values() for day in _holding_days_including_open(result)]
    return sum(days) / len(days) if days else 0.0


def _lockup_rate_including_open(result: BacktestResult, threshold: int) -> float:
    days = _holding_days_including_open(result)
    if not days:
        return 0.0
    return sum(day > threshold for day in days) / len(days) * 100


def _open_price_drawdown(result: BacktestResult) -> float:
    if int(result.open_position["quantity"]) <= 0:
        return 0.0
    average = float(result.open_position["average_price"])
    market = float(result.open_position["market_price"])
    if average <= 0:
        return 0.0
    return (market / average - 1.0) * 100


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
        "avg_holding_days_including_open": round(_average_holding_including_open(results), 2),
        "max_holding_days_worst_symbol_including_open": max(
            max(_holding_days_including_open(result), default=0) for result in results.values()
        ),
        "mae_p95_worst_symbol_pct": min(
            float(result.metrics["mae_p95_pct"]) for result in results.values()
        ),
        "worst_mae_pct": min(float(result.metrics["worst_mae_pct"]) for result in results.values()),
        "lockup_over_40_days_worst_symbol_pct": round(
            max(_lockup_rate_including_open(result, 40) for result in results.values()),
            2,
        ),
        "open_price_drawdown_worst_symbol_pct": round(
            min(_open_price_drawdown(result) for result in results.values()),
            2,
        ),
        "capital_utilization_avg_pct": round(
            sum(float(result.metrics["average_capital_utilization_pct"]) for result in results.values())
            / len(results),
            2,
        ),
    }


def settings(config) -> dict[str, Any]:
    guard = config.market_regime.get("soxl_sector_guard", {})
    return {
        "entry_score": config.global_.entry_score,
        "stage_weights": [float(value) for value in config.position.stage_weights],
        "stage_drops": [
            float(config.additional_entry.stages[stage].min_drop_from_anchor)
            for stage in (2, 3, 4)
        ],
        "tp": [float(config.take_profit.tp1_base), float(config.take_profit.tp2_base)],
        "rebuy_enabled": config.rebuy.enabled,
        "soxl_guard_blocked_stages": list(guard.get("blocked_stages", [])),
        "soxl_guard_rule": guard.get("rule"),
    }


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 2.0 Phase-4 SOXL First-Entry Guard Backtest",
        "",
        "Primary decision window: **2021-2024 validation**. Recent years are reference only.",
        "TQQQ logic is unchanged. Only the SOXL semiconductor guard and TP2 8%/6% are compared.",
        "Open positions at the end of each segment are included in holding/lockup risk.",
        "",
        (
            "| Candidate | CAGR | MDD | P95 MAE* | >40d lockup* | Max hold* | "
            "Open DD* | Cycles | Avg hold* |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
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
            f"{m['max_holding_days_worst_symbol_including_open']}d | "
            f"{m['open_price_drawdown_worst_symbol_pct']:.2f}% | "
            f"{m['closed_cycles']} | {m['avg_holding_days_including_open']:.1f}d |"
        )
    lines.extend(
        [
            "",
            (
                "\* P95 MAE uses closed cycles. Lockup/max hold/average hold include any open cycle. "
                "Open DD is the worse end-of-window price drawdown versus average cost among TQQQ/SOXL."
            ),
            "",
            "## Decision rule",
            "",
            "The stage-1 sector guard is useful only if it removes or materially reduces the SOXL "
            "2021-2024 stale-position failure while preserving validation CAGR and avoiding a new "
            "drawdown regime. The stricter `any` rule must earn its opportunity-cost versus `all`.",
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
