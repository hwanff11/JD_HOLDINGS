#!/usr/bin/env python3
"""JDSS 2.0 dual-track TP 4/6 vs 4/8 trend-filter exploration.

This phase keeps the live strategy untouched and explores first-entry filters in the
research harness. Both TP tracks receive exactly the same filter set so the TP
choice and the entry-filter choice can be evaluated independently.
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


def _with_tp(base, tp2: str):
    return replace(base, take_profit=replace(base.take_profit, tp1_base=Decimal("0.04"), tp2_base=Decimal(tp2)))


def _with_entry_score(base, score: int):
    return replace(base, global_=replace(base.global_, entry_score=score))


def _with_regime_red_block(base, enabled: bool):
    return replace(base, global_=replace(base.global_, red_blocks_new_entry=enabled))


def build_candidates(base):
    """Two TP tracks with identical, deliberately small first-entry filter search.

    We use filters already represented by the production configuration so this
    phase does not contaminate live strategy code with unproven indicators.
    Entry-score 55 is a stricter confirmation proxy; RED-block is the structural
    market trend gate. Their combination is the strict candidate.
    """
    candidates = {}
    for label, tp2 in (("TP46", "0.06"), ("TP48", "0.08")):
        track = _with_tp(base, tp2)
        candidates[f"{label}_F0_baseline"] = track
        candidates[f"{label}_F1_entry55"] = _with_entry_score(track, 55)
        candidates[f"{label}_F2_red_block"] = _with_regime_red_block(track, True)
        candidates[f"{label}_F3_entry55_red_block"] = _with_regime_red_block(_with_entry_score(track, 55), True)
    return candidates


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
    return (sum(day > threshold for day in days) / len(days) * 100) if days else 0.0


def _open_price_drawdown(result: BacktestResult) -> float:
    if int(result.open_position["quantity"]) <= 0:
        return 0.0
    average = float(result.open_position["average_price"])
    market = float(result.open_position["market_price"])
    return (market / average - 1.0) * 100 if average > 0 else 0.0


def combined_metrics(results: dict[str, BacktestResult], annualization_days: int) -> dict[str, Any]:
    equity = pd.concat([r.equity_curve.rename(s) for s, r in results.items()], axis=1, join="inner").sum(axis=1)
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    return {
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3), "sortino": round(sortino, 3),
        "closed_cycles": sum(int(r.metrics["closed_cycles"]) for r in results.values()),
        "signals": sum(int(r.metrics["signals"]) for r in results.values()),
        "avg_holding_days_including_open": round(_average_holding_including_open(results), 2),
        "max_holding_days_worst_symbol_including_open": max(max(_holding_days_including_open(r), default=0) for r in results.values()),
        "mae_p95_worst_symbol_pct": min(float(r.metrics["mae_p95_pct"]) for r in results.values()),
        "worst_mae_pct": min(float(r.metrics["worst_mae_pct"]) for r in results.values()),
        "lockup_over_40_days_worst_symbol_pct": round(max(_lockup_rate_including_open(r, 40) for r in results.values()), 2),
        "open_price_drawdown_worst_symbol_pct": round(min(_open_price_drawdown(r) for r in results.values()), 2),
    }


def settings(config) -> dict[str, Any]:
    return {
        "entry_score": config.global_.entry_score,
        "red_blocks_new_entry": config.global_.red_blocks_new_entry,
        "tp": [float(config.take_profit.tp1_base), float(config.take_profit.tp2_base)],
    }


def markdown_summary(report: dict[str, Any]) -> str:
    lines = ["# JDSS 2.0 Dual-Track First-Entry Filter Search", "",
             "Primary decision window: **2021-2024 validation**. TP 4/6 and TP 4/8 are evaluated as separate tracks with identical filters.", "",
             "| Candidate | CAGR | MDD | P95 MAE* | >40d lockup* | Max hold* | Open DD* | Cycles | Avg hold* |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    rows = []
    for name, c in report["candidates"].items():
        m = c["segments"]["validation_2021_2024"]["combined"]
        rows.append((name, m))
    for name, m in rows:
        lines.append(f"| {name} | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | {m['mae_p95_worst_symbol_pct']:.2f}% | {m['lockup_over_40_days_worst_symbol_pct']:.2f}% | {m['max_holding_days_worst_symbol_including_open']}d | {m['open_price_drawdown_worst_symbol_pct']:.2f}% | {m['closed_cycles']} | {m['avg_holding_days_including_open']:.1f}d |")
    lines += ["", "## Selection rule", "",
              "Select the best candidate inside each TP track first. Only then compare TP4/6 versus TP4/8. A filter is rejected if CAGR improvement comes with unresolved multi-year lockup or materially worse MAE/MDD."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "v2_focused_backtest.json")
    args = parser.parse_args()
    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)).isoformat()
    frames = {s: source.daily(s, warmup, args.end, refresh=True) for s in (*SYMBOLS, *BENCHMARKS)}
    segments = {
        "development_2011_2020": ("2011-01-01", "2020-12-31"),
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "recent_2025_present": ("2025-01-01", args.end),
        "full_history": ("2011-01-01", args.end),
    }
    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "data_end": args.end, "candidates": {}}
    for name, config in build_candidates(base).items():
        candidate: dict[str, Any] = {"settings": settings(config), "segments": {}}
        for segment_name, (start, end) in segments.items():
            results = {symbol: BacktestEngine(config).run(symbol, frames[symbol], frames["SPY"], frames["QQQ"], start=start, end=end, slippage=base.backtest.default_slippage, sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]} if symbol == "SOXL" else None) for symbol in SYMBOLS}
            candidate["segments"][segment_name] = {"combined": combined_metrics(results, config.backtest.annualization_days), "symbols": {s: r.metrics for s, r in results.items()}}
        report["candidates"][name] = candidate
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = args.output.with_suffix(".md")
    summary.write_text(markdown_summary(report), encoding="utf-8")
    print(summary.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
