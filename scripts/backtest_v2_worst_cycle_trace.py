#!/usr/bin/env python3
"""Trace the longest full-history cycle for the JDSS FINAL strategy."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from backtest_v2_focus import (
    BENCHMARKS,
    SYMBOLS,
    _with_entry_score,
    _with_stage1_guard,
    _with_tp,
)
from backtest_v2_remainder_exit import RemainderExitEngine

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


def _final(base):
    return _with_stage1_guard(_with_entry_score(_with_tp(base, "0.06"), 55))


def _trace(result):
    candidates = list(result.closed_cycles)
    open_cycle = result.metrics.get("open_cycle")
    if open_cycle:
        candidates.append(open_cycle)
    if not candidates:
        return None
    cycle = max(candidates, key=lambda item: int(item.get("holding_days", 0)))
    cycle_id = cycle.get("cycle_id")
    trades = [trade for trade in result.trades if trade.get("cycle_id") == cycle_id]
    signals = [signal for signal in result.signals if signal.get("cycle_id") == cycle_id]
    return {
        "symbol": result.symbol,
        "cycle": cycle,
        "trades": trades,
        "signals": signals,
        "tp1_seen": any(trade.get("purpose") == "TP1" for trade in trades),
        "tp2_seen": any(trade.get("purpose") == "TP2" for trade in trades),
        "remainder_exit_seen": any(
            str(trade.get("purpose", "")).startswith("TP1_REMAINDER") for trade in trades
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "v2_worst_cycle_trace.json"
    )
    args = parser.parse_args()
    base = load_config(ROOT / "strategy.yaml")
    config = _final(base)
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)
    ).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup_start, args.end, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    traces = []
    for symbol in SYMBOLS:
        sector_data = (
            {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
            if symbol == "SOXL"
            else None
        )
        engine = RemainderExitEngine(
            config, wait_days=20, target_pct=Decimal("0.02")
        )
        result = engine.run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start="2011-01-01",
            end=args.end,
            slippage=base.backtest.default_slippage,
            sector_data=sector_data,
        )
        trace = _trace(result)
        if trace:
            traces.append(trace)
    traces.sort(key=lambda item: int(item["cycle"].get("holding_days", 0)), reverse=True)
    report = {"generated_at": datetime.now(UTC).isoformat(), "traces": traces}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# JDSS FINAL Full-History Worst-Cycle Trace", ""]
    for trace in traces:
        cycle = trace["cycle"]
        lines.append(
            f"- {trace['symbol']}: {cycle.get('start_date')} -> {cycle.get('end_date')}, "
            f"{cycle.get('holding_days')}d, entries={cycle.get('entry_count')}, "
            f"MAE={float(cycle.get('mae', 0)) * 100:.2f}%, "
            f"TP1={trace['tp1_seen']}, TP2={trace['tp2_seen']}, "
            f"remainder_exit={trace['remainder_exit_seen']}"
        )
        for trade in trace["trades"]:
            lines.append(
                f"  - {trade['date']} {trade['side']} {trade['purpose']} "
                f"qty={trade['quantity']} price={trade['price']} "
                f"avg={trade.get('average_price', '-')}"
            )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
