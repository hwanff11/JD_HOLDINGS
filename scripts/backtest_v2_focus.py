#!/usr/bin/env python3
"""JDSS 2.0 dual-track TP 4/6 vs 4/8 first-entry exploration."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


def _with_tp(base, tp2):
    return replace(
        base,
        take_profit=replace(
            base.take_profit,
            tp1_base=Decimal("0.04"),
            tp2_base=Decimal(tp2),
        ),
    )


def _with_entry_score(base, score):
    return replace(base, global_=replace(base.global_, entry_score=score))


def _with_stage1_guard(base, rule="any_benchmark_below_ema60"):
    guard = dict(base.market_regime.get("soxl_sector_guard", {}))
    blocked = {int(x) for x in guard.get("blocked_stages", (3, 4))}
    blocked.add(1)
    guard["blocked_stages"] = sorted(blocked)
    guard["rule"] = rule
    return replace(
        base,
        market_regime={**base.market_regime, "soxl_sector_guard": guard},
    )


def build_candidates(base):
    out = {}
    for label, tp2 in (("TP46", "0.06"), ("TP48", "0.08")):
        track = _with_tp(base, tp2)
        out[f"{label}_F0_baseline"] = track
        out[f"{label}_F1_entry55"] = _with_entry_score(track, 55)
        out[f"{label}_F2_entry60"] = _with_entry_score(track, 60)
        out[f"{label}_F3_soxl_stage1_guard"] = _with_stage1_guard(track)
        out[f"{label}_F4_entry55_guard"] = _with_stage1_guard(
            _with_entry_score(track, 55)
        )
    return out


def _days(result):
    days = [int(cycle["holding_days"]) for cycle in result.closed_cycles]
    if int(result.open_position["quantity"]) > 0:
        days.append(int(result.open_position["holding_days"]))
    return days


def _open_dd(result):
    if int(result.open_position["quantity"]) <= 0:
        return 0.0
    average = float(result.open_position["average_price"])
    market = float(result.open_position["market_price"])
    return (market / average - 1) * 100 if average > 0 else 0.0


def combined_metrics(results, annualization_days):
    equity = pd.concat(
        [result.equity_curve.rename(symbol) for symbol, result in results.items()],
        axis=1,
        join="inner",
    ).sum(axis=1)
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    years = max(
        (equity.index[-1] - equity.index[0]).days / 365.2425,
        1 / 365.2425,
    )
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    all_days = [day for result in results.values() for day in _days(result)]
    lockup_rates = []
    for result in results.values():
        result_days = _days(result)
        rate = (
            sum(day > 40 for day in result_days) / len(result_days) * 100
            if result_days
            else 0
        )
        lockup_rates.append(rate)

    return {
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "closed_cycles": sum(
            int(result.metrics["closed_cycles"]) for result in results.values()
        ),
        "signals": sum(int(result.metrics["signals"]) for result in results.values()),
        "avg_holding_days_including_open": (
            round(sum(all_days) / len(all_days), 2) if all_days else 0.0
        ),
        "max_holding_days_worst_symbol_including_open": max(
            (max(_days(result), default=0) for result in results.values()),
            default=0,
        ),
        "mae_p95_worst_symbol_pct": min(
            float(result.metrics["mae_p95_pct"]) for result in results.values()
        ),
        "worst_mae_pct": min(
            float(result.metrics["worst_mae_pct"]) for result in results.values()
        ),
        "lockup_over_40_days_worst_symbol_pct": round(max(lockup_rates), 2),
        "open_price_drawdown_worst_symbol_pct": round(
            min(_open_dd(result) for result in results.values()),
            2,
        ),
    }


def settings(config):
    guard = config.market_regime.get("soxl_sector_guard", {})
    return {
        "entry_score": config.global_.entry_score,
        "tp": [
            float(config.take_profit.tp1_base),
            float(config.take_profit.tp2_base),
        ],
        "soxl_guard_stages": list(guard.get("blocked_stages", [])),
        "soxl_guard_rule": guard.get("rule"),
    }


def markdown_summary(report):
    lines = [
        "# JDSS 2.0 Dual-Track Filter Search",
        "",
        "TP 4/6 and TP 4/8 are evaluated independently with the same "
        "confirmation/guard candidates.",
        "",
        "| Candidate | CAGR | MDD | P95 MAE | >40d lockup | Max hold | "
        "Open DD | Cycles | Avg hold |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, candidate in report["candidates"].items():
        metrics = candidate["segments"]["validation_2021_2024"]["combined"]
        lines.append(
            f"| {name} | {metrics['cagr_pct']:+.2f}% | "
            f"{metrics['mdd_pct']:.2f}% | "
            f"{metrics['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{metrics['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{metrics['max_holding_days_worst_symbol_including_open']}d | "
            f"{metrics['open_price_drawdown_worst_symbol_pct']:.2f}% | "
            f"{metrics['closed_cycles']} | "
            f"{metrics['avg_holding_days_including_open']:.1f}d |"
        )
    lines += [
        "",
        "## Selection rule",
        "",
        "Pick the best filter inside each TP track first; then compare the two "
        "track winners. Reject candidates that leave the multi-year lockup "
        "unresolved unless they materially improve the risk/return profile.",
    ]
    return "\n".join(lines) + "\n"


def main():
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
    warmup_start = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)
    ).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup_start, args.end, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    segments = {
        "development_2011_2020": ("2011-01-01", "2020-12-31"),
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "recent_2025_present": ("2025-01-01", args.end),
        "full_history": ("2011-01-01", args.end),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_end": args.end,
        "candidates": {},
    }

    for name, config in build_candidates(base).items():
        candidate = {"settings": settings(config), "segments": {}}
        for segment_name, (start, end) in segments.items():
            results = {}
            for symbol in SYMBOLS:
                sector_data = (
                    {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None
                )
                results[symbol] = BacktestEngine(config).run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=base.backtest.default_slippage,
                    sector_data=sector_data,
                )
            candidate["segments"][segment_name] = {
                "combined": combined_metrics(
                    results,
                    config.backtest.annualization_days,
                ),
                "symbols": {
                    symbol: result.metrics for symbol, result in results.items()
                },
            }
        report["candidates"][name] = candidate

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(markdown_summary(report), encoding="utf-8")
    print(markdown_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
