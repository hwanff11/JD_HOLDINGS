#!/usr/bin/env python3
"""Audit JDSS 2.0 backtest semantics before further strategy tuning."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.config import load_config
from jd_holdings.core.enums import DecisionType
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")
START = "2021-01-01"
END = "2024-12-31"


def _candidate_configs(base):
    return {
        "A_entry50": base,
        "B_entry55": replace(base, global_=replace(base.global_, entry_score=55)),
    }


def _counter(items) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))


def _audit_result(result: BacktestResult) -> dict[str, Any]:
    signal_actions = _counter(signal["action"] for signal in result.signals)
    buy_purposes = _counter(
        trade["purpose"] for trade in result.trades if trade["side"] == "BUY"
    )
    sell_purposes = _counter(
        trade["purpose"] for trade in result.trades if trade["side"] == "SELL"
    )
    skipped_reasons = _counter(item["reason"] for item in result.skipped_signals)

    first_signal_count = signal_actions.get(DecisionType.FIRST_ENTRY_CANDIDATE.value, 0)
    first_buy_count = buy_purposes.get(DecisionType.FIRST_ENTRY_CANDIDATE.value, 0)
    add_signal_count = signal_actions.get(DecisionType.ADD_ENTRY_CANDIDATE.value, 0)
    add_buy_count = buy_purposes.get(DecisionType.ADD_ENTRY_CANDIDATE.value, 0)

    longest_cycles = sorted(
        result.closed_cycles,
        key=lambda cycle: int(cycle["holding_days"]),
        reverse=True,
    )[:5]

    checks = {
        "metric_signal_count_matches_details": int(result.metrics["signals"]) == len(result.signals),
        "first_entry_buys_not_above_signals": first_buy_count <= first_signal_count,
        "closed_cycles_not_above_first_entries": int(result.metrics["closed_cycles"])
        <= int(result.metrics["executed_entries"]),
        "rebuy_disabled_no_rebuy_signal": signal_actions.get(
            DecisionType.REBUY_CANDIDATE.value, 0
        )
        == 0,
        "tp2_hits_match_closed_cycles_when_rebuy_disabled": int(result.metrics["tp2_hits"])
        == int(result.metrics["closed_cycles"]),
    }

    return {
        "metrics": result.metrics,
        "signal_actions": signal_actions,
        "buy_purposes": buy_purposes,
        "sell_purposes": sell_purposes,
        "skipped_reasons": skipped_reasons,
        "first_entry_signals": first_signal_count,
        "first_entry_buys": first_buy_count,
        "additional_entry_signals": add_signal_count,
        "additional_entry_buys": add_buy_count,
        "longest_closed_cycles": longest_cycles,
        "open_position": result.open_position,
        "checks": checks,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 2.0 Backtest Audit",
        "",
        "Validation window: **2021-01-01 ~ 2024-12-31**",
        "",
        "This report separates first-entry signals from additional-entry signals. "
        "The existing `signals` metric counts every allowed strategy decision, not only new entries.",
        "",
        "| Candidate | Symbol | Total signals | First signals | First buys | Add signals | Add buys | Closed | Max hold |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate_name, symbols in report["candidates"].items():
        for symbol, audit in symbols.items():
            metrics = audit["metrics"]
            lines.append(
                f"| {candidate_name} | {symbol} | {metrics['signals']} | "
                f"{audit['first_entry_signals']} | {audit['first_entry_buys']} | "
                f"{audit['additional_entry_signals']} | {audit['additional_entry_buys']} | "
                f"{metrics['closed_cycles']} | {metrics['maximum_holding_days']} |"
            )

    lines.extend(["", "## Longest cycles", ""])
    for candidate_name, symbols in report["candidates"].items():
        for symbol, audit in symbols.items():
            lines.append(f"### {candidate_name} / {symbol}")
            cycles = audit["longest_closed_cycles"]
            if not cycles:
                lines.append("- No closed cycles")
            for cycle in cycles:
                lines.append(
                    f"- {cycle['cycle_id']}: {cycle['start_date']} → {cycle['end_date']}, "
                    f"{cycle['holding_days']} trading days, PnL ${cycle['pnl']}, "
                    f"entries={cycle['entry_count']}, MAE={cycle['mae'] * 100:.2f}%"
                )
            open_position = audit["open_position"]
            if int(open_position["quantity"]) > 0:
                lines.append(
                    f"- OPEN at period end: state={open_position['state']}, "
                    f"holding_days={open_position['holding_days']}, "
                    f"MAE={open_position['mae_pct']:.2f}%"
                )
            lines.append("")

    lines.extend(["## Invariant checks", ""])
    for candidate_name, symbols in report["candidates"].items():
        for symbol, audit in symbols.items():
            failed = [name for name, passed in audit["checks"].items() if not passed]
            status = "PASS" if not failed else "FAIL: " + ", ".join(failed)
            lines.append(f"- {candidate_name} / {symbol}: {status}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_backtest_audit.json",
    )
    args = parser.parse_args()

    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = (date.fromisoformat(START) - timedelta(days=400)).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup_start, END, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }

    report: dict[str, Any] = {"window": [START, END], "candidates": {}}
    for candidate_name, config in _candidate_configs(base).items():
        candidate_report: dict[str, Any] = {}
        for symbol in SYMBOLS:
            result = BacktestEngine(config).run(
                symbol,
                frames[symbol],
                frames["SPY"],
                frames["QQQ"],
                start=START,
                end=END,
                slippage=base.backtest.default_slippage,
                sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                if symbol == "SOXL"
                else None,
            )
            candidate_report[symbol] = _audit_result(result)
        report["candidates"][candidate_name] = candidate_report

    failed_checks = [
        f"{candidate}/{symbol}/{name}"
        for candidate, symbols in report["candidates"].items()
        for symbol, audit in symbols.items()
        for name, passed in audit["checks"].items()
        if not passed
    ]
    report["failed_checks"] = failed_checks

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = args.output.with_suffix(".md")
    summary.write_text(_markdown(report), encoding="utf-8")
    print(summary.read_text(encoding="utf-8"))
    if failed_checks:
        print("failed_checks=" + ",".join(failed_checks))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
