#!/usr/bin/env python3
"""Audit the selected D257 FINAL candidate and report calendar-year returns."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backtest_v2_gap_grid import BENCHMARKS, SYMBOLS, _candidate, _run

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


def annual(results, years):
    output = {}
    for year in years:
        values = {}
        for symbol, result in results.items():
            equity = result.equity_curve[result.equity_curve.index.year == year]
            if not equity.empty:
                values[symbol] = (
                    float(equity.iloc[-1]) / float(equity.iloc[0]) - 1
                ) * 100
        output[str(year)] = {
            "combined_pct": sum(values.values()) / len(values) if values else None,
            "symbols": values,
        }
    return output


def worst(results):
    rows = []
    for symbol, result in results.items():
        cycles = list(result.closed_cycles)
        if result.metrics.get("open_cycle"):
            cycles.append(result.metrics["open_cycle"])
        for cycle in cycles:
            rows.append((int(cycle.get("holding_days", 0)), symbol, cycle))
    days, symbol, cycle = max(rows, key=lambda item: item[0])
    result = results[symbol]
    cycle_id = cycle.get("cycle_id")
    return {
        "symbol": symbol,
        "holding_days": days,
        "cycle": cycle,
        "trades": [
            trade for trade in result.trades if trade.get("cycle_id") == cycle_id
        ],
        "signals": [
            signal for signal in result.signals if signal.get("cycle_id") == cycle_id
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_final_d257_report.json",
    )
    args = parser.parse_args()

    base = load_config(ROOT / "strategy.yaml")
    config = _candidate(base, (0.02, 0.05, 0.07))
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)
    ).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup, args.end, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    results = _run(config, frames, "2011-01-01", args.end)
    years = range(2011, datetime.fromisoformat(args.end).year + 1)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": (
            "scores 55/55/55/55; weights 40/30/20/10; drops -2/-5/-7; "
            "TP 4/6; SOXL sector guard stages 1/3/4; TP1 remainder 20d avg+2%"
        ),
        "worst_cycle": worst(results),
        "annual_returns": annual(results, years),
        "symbols": {symbol: result.metrics for symbol, result in results.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    worst_cycle = report["worst_cycle"]
    cycle = worst_cycle["cycle"]
    lines = [
        "# JDSS FINAL D257 Report",
        "",
        (
            f"Worst cycle: {worst_cycle['symbol']} {cycle.get('start_date')} -> "
            f"{cycle.get('end_date')} ({worst_cycle['holding_days']} trading days), "
            f"MAE={float(cycle.get('mae', 0)) * 100:.2f}%"
        ),
        "",
        "## Trades",
    ]
    for trade in worst_cycle["trades"]:
        lines.append(
            f"- {trade.get('date')} {trade.get('side')} {trade.get('purpose')} "
            f"price={trade.get('price')} qty={trade.get('quantity')} "
            f"avg={trade.get('average_price', '-')} score={trade.get('score', '-')}"
        )
    lines.extend(
        [
            "",
            "## Calendar-year returns",
            "",
            "| Year | Combined | TQQQ | SOXL |",
            "|---:|---:|---:|---:|",
        ]
    )
    for year in years:
        item = report["annual_returns"][str(year)]
        combined = item["combined_pct"]
        if combined is None:
            continue
        lines.append(
            f"| {year} | {combined:+.2f}% | "
            f"{item['symbols'].get('TQQQ', 0):+.2f}% | "
            f"{item['symbols'].get('SOXL', 0):+.2f}% |"
        )
    args.output.with_suffix(".md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
