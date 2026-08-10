#!/usr/bin/env python3
"""Trace the longest validation cycles for the current TP4/6 and TP4/8 research leaders."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from backtest_v2_focus import BENCHMARKS, SYMBOLS, _with_entry_score, _with_stage1_guard, _with_tp
from backtest_v2_remainder_exit import RemainderExitEngine

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


def _champion(base, tp2: str):
    return _with_stage1_guard(_with_entry_score(_with_tp(base, tp2), 55))


def _run_candidate(base, frames, *, tp2: str, wait_days: int, target_pct: Decimal):
    config = _champion(base, tp2)
    results = {}
    for symbol in SYMBOLS:
        sector_data = (
            {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
            if symbol == "SOXL"
            else None
        )
        engine = RemainderExitEngine(config, wait_days=wait_days, target_pct=target_pct)
        results[symbol] = engine.run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start="2021-01-01",
            end="2024-12-31",
            slippage=base.backtest.default_slippage,
            sector_data=sector_data,
        )
    return results


def _trace(result):
    if not result.closed_cycles:
        return None
    cycle = max(result.closed_cycles, key=lambda item: int(item["holding_days"]))
    cycle_id = cycle["cycle_id"]
    trades = [trade for trade in result.trades if trade.get("cycle_id") == cycle_id]
    signals = [signal for signal in result.signals if signal.get("cycle_id") == cycle_id]
    return {
        "symbol": result.symbol,
        "cycle": cycle,
        "trades": trades,
        "signals": signals,
        "trade_purposes": [trade.get("purpose") for trade in trades],
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
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = (
        datetime.fromisoformat("2021-01-01").date() - timedelta(days=400)
    ).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup_start, "2024-12-31", refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    candidates = {
        "TP46_R20_P02": ("0.06", 20, Decimal("0.02")),
        "TP48_R40_P00": ("0.08", 40, Decimal("0.00")),
    }
    report = {"generated_at": datetime.now(UTC).isoformat(), "candidates": {}}
    for name, (tp2, wait_days, target_pct) in candidates.items():
        results = _run_candidate(
            base, frames, tp2=tp2, wait_days=wait_days, target_pct=target_pct
        )
        traces = [trace for result in results.values() if (trace := _trace(result))]
        traces.sort(key=lambda item: int(item["cycle"]["holding_days"]), reverse=True)
        report["candidates"][name] = traces

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# JDSS 2.0 Worst-Cycle Trace", ""]
    for name, traces in report["candidates"].items():
        lines.extend([f"## {name}", ""])
        for trace in traces:
            cycle = trace["cycle"]
            lines.append(
                f"- {trace['symbol']}: {cycle['start_date']} -> {cycle['end_date']}, "
                f"{cycle['holding_days']}d, entries={cycle['entry_count']}, "
                f"MAE={cycle['mae'] * 100:.2f}%, TP1={trace['tp1_seen']}, "
                f"remainder_exit={trace['remainder_exit_seen']}"
            )
            for trade in trace["trades"]:
                lines.append(
                    f"  - {trade['date']} {trade['side']} {trade['purpose']} "
                    f"qty={trade['quantity']} price={trade['price']} "
                    f"avg={trade.get('average_price', '-') }"
                )
        lines.append("")
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
