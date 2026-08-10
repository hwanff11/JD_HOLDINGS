#!/usr/bin/env python3
"""Audit JDSS 2.0 backtest semantics before further strategy tuning."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
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


def _with_tp(base, tp1: str, tp2: str):
    return replace(
        base,
        take_profit=replace(
            base.take_profit,
            tp1_base=Decimal(tp1),
            tp2_base=Decimal(tp2),
        ),
    )


def _candidate_configs(base):
    return {
        "A_entry50": base,
        "B_entry55": replace(base, global_=replace(base.global_, entry_score=55)),
        "G_full_exit_tp44": _with_tp(base, "0.04", "0.04"),
        "H_full_exit_tp33": _with_tp(base, "0.03", "0.03"),
    }


def _counter(items) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))


def _lockup_rate(holding_days: list[int], threshold: int) -> float:
    if not holding_days:
        return 0.0
    return round(sum(day > threshold for day in holding_days) / len(holding_days) * 100, 2)


def _open_cycle_details(result: BacktestResult) -> dict[str, Any] | None:
    if int(result.open_position["quantity"]) <= 0:
        return None

    closed_ids = {str(cycle["cycle_id"]) for cycle in result.closed_cycles}
    first_buys = [
        trade
        for trade in result.trades
        if trade["side"] == "BUY"
        and trade["purpose"] == DecisionType.FIRST_ENTRY_CANDIDATE.value
        and str(trade["cycle_id"]) not in closed_ids
    ]
    if not first_buys:
        return None

    first = first_buys[-1]
    cycle_id = str(first["cycle_id"])
    cycle_trades = [trade for trade in result.trades if str(trade["cycle_id"]) == cycle_id]
    return {
        "cycle_id": cycle_id,
        "start_date": first["date"],
        "first_entry_score": int(first.get("score", 0)),
        "first_entry_price": float(first["price"]),
        "holding_days": int(result.open_position["holding_days"]),
        "state": result.open_position["state"],
        "quantity": int(result.open_position["quantity"]),
        "average_price": float(result.open_position["average_price"]),
        "market_price": float(result.open_position["market_price"]),
        "account_mae_pct": float(result.open_position["mae_pct"]),
        "price_vs_average_pct": round(
            (float(result.open_position["market_price"]) / float(result.open_position["average_price"]) - 1)
            * 100,
            2,
        )
        if float(result.open_position["average_price"]) > 0
        else 0.0,
        "buy_count": sum(trade["side"] == "BUY" for trade in cycle_trades),
        "sell_count": sum(trade["side"] == "SELL" for trade in cycle_trades),
        "purposes": _counter(str(trade["purpose"]) for trade in cycle_trades),
    }


def _closed_cycle_details(result: BacktestResult) -> list[dict[str, Any]]:
    first_buy_by_cycle = {
        str(trade["cycle_id"]): trade
        for trade in result.trades
        if trade["side"] == "BUY"
        and trade["purpose"] == DecisionType.FIRST_ENTRY_CANDIDATE.value
    }
    details = []
    for cycle in result.closed_cycles:
        item = dict(cycle)
        first = first_buy_by_cycle.get(str(cycle["cycle_id"]))
        item["first_entry_score"] = int(first.get("score", 0)) if first else 0
        item["first_entry_price"] = float(first["price"]) if first else 0.0
        details.append(item)
    return sorted(details, key=lambda cycle: int(cycle["holding_days"]), reverse=True)[:5]


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

    longest_cycles = _closed_cycle_details(result)
    open_cycle = _open_cycle_details(result)
    closed_holding_days = [int(cycle["holding_days"]) for cycle in result.closed_cycles]
    holding_days_including_open = list(closed_holding_days)
    if open_cycle:
        holding_days_including_open.append(int(open_cycle["holding_days"]))

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
        "open_cycle": open_cycle,
        "holding_risk_including_open": {
            "maximum_holding_days": max(holding_days_including_open, default=0),
            "lockup_over_20_days_pct": _lockup_rate(holding_days_including_open, 20),
            "lockup_over_40_days_pct": _lockup_rate(holding_days_including_open, 40),
            "lockup_over_60_days_pct": _lockup_rate(holding_days_including_open, 60),
            "cycle_count_including_open": len(holding_days_including_open),
        },
        "checks": checks,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 2.0 Backtest Audit",
        "",
        "Validation window: **2021-01-01 ~ 2024-12-31**",
        "",
        "The existing `signals` metric counts every allowed strategy decision, not only new entries. "
        "This audit also recalculates lockup statistics including an open position at period end.",
        "",
        (
            "| Candidate | Symbol | Total signals | First signals | First buys | Add signals | "
            "Add buys | Closed | Max hold closed | Max hold incl. open |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate_name, symbols in report["candidates"].items():
        for symbol, audit in symbols.items():
            metrics = audit["metrics"]
            open_risk = audit["holding_risk_including_open"]
            lines.append(
                f"| {candidate_name} | {symbol} | {metrics['signals']} | "
                f"{audit['first_entry_signals']} | {audit['first_entry_buys']} | "
                f"{audit['additional_entry_signals']} | {audit['additional_entry_buys']} | "
                f"{metrics['closed_cycles']} | {metrics['maximum_holding_days']} | "
                f"{open_risk['maximum_holding_days']} |"
            )

    lines.extend(["", "## Lockup risk including open positions", ""])
    for candidate_name, symbols in report["candidates"].items():
        for symbol, audit in symbols.items():
            closed = audit["metrics"]
            adjusted = audit["holding_risk_including_open"]
            lines.append(
                f"- {candidate_name} / {symbol}: >40d closed-only "
                f"{closed['lockup_over_40_days_pct']:.2f}% → including-open "
                f"{adjusted['lockup_over_40_days_pct']:.2f}%"
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
                    f"{cycle['holding_days']} trading days, entry-score={cycle['first_entry_score']}, "
                    f"PnL ${cycle['pnl']}, entries={cycle['entry_count']}, "
                    f"MAE={cycle['mae'] * 100:.2f}%"
                )
            open_cycle = audit["open_cycle"]
            if open_cycle:
                lines.append(
                    f"- OPEN {open_cycle['cycle_id']}: start={open_cycle['start_date']}, "
                    f"entry-score={open_cycle['first_entry_score']}, state={open_cycle['state']}, "
                    f"holding={open_cycle['holding_days']}d, "
                    f"price-vs-avg={open_cycle['price_vs_average_pct']:.2f}%, "
                    f"account-MAE={open_cycle['account_mae_pct']:.2f}%, "
                    f"buys={open_cycle['buy_count']}, sells={open_cycle['sell_count']}"
                )
            lines.append("")

    lines.extend(
        [
            "## Stateful threshold finding",
            "",
            (
                "A stricter entry score can still produce more later first-entry signals because the "
                "strategy is stateful. An earlier low-threshold entry can remain open for years and "
                "block all later first entries, while a stricter candidate can skip that cycle and "
                "become EMPTY sooner for future signals. Compare the open/long cycles above before "
                "interpreting signal totals."
            ),
            "",
            "## Invariant checks",
            "",
        ]
    )
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
