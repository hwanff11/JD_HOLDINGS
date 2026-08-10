#!/usr/bin/env python3
"""Test non-stop-loss exits for capital locked after TP1."""

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
    combined_metrics,
)

from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.enums import PositionState
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


class RemainderExitEngine(BacktestEngine):
    """Research-only engine that relaxes TP2 after TP1 without selling at a loss."""

    def __init__(self, config, *, wait_days: int | None, target_pct: Decimal) -> None:
        super().__init__(config)
        self.wait_days = wait_days
        self.target_pct = target_pct
        self._tp1_day: dict[str, int] = {}

    def _process_take_profit(self, state, timestamp, high, trades, closed_cycles):
        cycle_id = state.cycle_id
        tp1_hits, tp2_hits, profitable_rebuy = super()._process_take_profit(
            state, timestamp, high, trades, closed_cycles
        )
        if tp1_hits and cycle_id:
            self._tp1_day[cycle_id] = state.cycle_holding_days
        if (
            self.wait_days is None
            or state.quantity <= 0
            or state.state != PositionState.PARTIAL_TP_1
            or not state.tp1_done
            or not state.cycle_id
        ):
            return tp1_hits, tp2_hits, profitable_rebuy

        tp1_day = self._tp1_day.get(state.cycle_id)
        if tp1_day is None or state.cycle_holding_days - tp1_day < self.wait_days:
            return tp1_hits, tp2_hits, profitable_rebuy

        exit_price = state.average_price * (Decimal("1") + self.target_pct)
        if high < exit_price:
            return tp1_hits, tp2_hits, profitable_rebuy

        used_rebuy = state.cycle_used_rebuy
        cycle_id = state.cycle_id
        self._sell(
            state,
            timestamp,
            exit_price,
            state.quantity,
            f"TP1_REMAINDER_{self.wait_days}D_{self.target_pct}",
            trades,
        )
        closed_cycles.append(
            {
                "cycle_id": cycle_id,
                "start_date": state.cycle_start_date.isoformat() if state.cycle_start_date else None,
                "end_date": timestamp.date().isoformat(),
                "holding_days": state.cycle_holding_days,
                "pnl": round(float(state.cycle_cashflows), 2),
                "mae": state.cycle_mae,
                "mfe": state.cycle_mfe,
                "entry_count": state.entry_count,
                "used_rebuy": used_rebuy,
            }
        )
        if used_rebuy and state.cycle_cashflows > 0:
            profitable_rebuy += 1
        self._tp1_day.pop(cycle_id, None)
        self._reset_cycle(state)
        return tp1_hits, tp2_hits, profitable_rebuy


def build_candidates(base):
    candidates = {}
    for label, tp2 in (("TP46", "0.06"), ("TP48", "0.08")):
        champion = _with_stage1_guard(_with_entry_score(_with_tp(base, tp2), 55))
        candidates[f"{label}_CHAMPION"] = (champion, None, Decimal("0"))
        for wait_days in (20, 40, 60):
            for target in (Decimal("0"), Decimal("0.02")):
                name = f"{label}_R{wait_days}_P{int(target * 100):02d}"
                candidates[name] = (champion, wait_days, target)
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "v2_remainder_exit.json"
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
    report = {"generated_at": datetime.now(UTC).isoformat(), "candidates": {}}

    for name, (config, wait_days, target_pct) in build_candidates(base).items():
        item = {
            "settings": {
                "tp2": float(config.take_profit.tp2_base),
                "entry_score": config.global_.entry_score,
                "wait_days_after_tp1": wait_days,
                "remainder_target_pct": float(target_pct),
            },
            "segments": {},
        }
        for segment_name, (start, end) in segments.items():
            results = {}
            for symbol in SYMBOLS:
                sector_data = (
                    {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None
                )
                engine = RemainderExitEngine(
                    config, wait_days=wait_days, target_pct=target_pct
                )
                results[symbol] = engine.run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=base.backtest.default_slippage,
                    sector_data=sector_data,
                )
            item["segments"][segment_name] = {
                "combined": combined_metrics(results, config.backtest.annualization_days),
                "symbols": {
                    symbol: {
                        "metrics": result.metrics,
                        "open_position": result.open_position,
                        "longest_cycles": sorted(
                            result.closed_cycles,
                            key=lambda cycle: int(cycle["holding_days"]),
                            reverse=True,
                        )[:5],
                    }
                    for symbol, result in results.items()
                },
            }
        report["candidates"][name] = item

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# JDSS 2.0 Post-TP1 Remainder Exit Search",
        "",
        (
            "Research-only: after TP1, wait N trading days and exit the remainder "
            "only if price recovers to average cost or average cost +2%. "
            "No stop-loss is used."
        ),
        "",
        "| Candidate | CAGR | MDD | P95 MAE | >40d | Max hold | Open DD | Cycles |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, item in report["candidates"].items():
        m = item["segments"]["validation_2021_2024"]["combined"]
        lines.append(
            f"| {name} | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | "
            f"{m['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{m['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{m['max_holding_days_worst_symbol_including_open']}d | "
            f"{m['open_price_drawdown_worst_symbol_pct']:.2f}% | "
            f"{m['closed_cycles']} |"
        )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
