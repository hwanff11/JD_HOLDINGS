#!/usr/bin/env python3
"""Test non-stop-loss exits for capital locked after TP1."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
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
from jd_holdings.core.remainder_exit import remainder_exit_due, remainder_exit_price
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


class RemainderExitEngine(BacktestEngine):
    """Backtest engine using the same post-TP1 remainder rule as live trading."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self._tp1_day: dict[str, int] = {}

    def _process_take_profit(self, state, timestamp, high, trades, closed_cycles):
        cycle_id = state.cycle_id
        tp1_hits, tp2_hits, profitable_rebuy = super()._process_take_profit(
            state, timestamp, high, trades, closed_cycles
        )
        if tp1_hits and cycle_id and state.quantity > 0:
            self._tp1_day[cycle_id] = state.cycle_holding_days

        rule = self.config.take_profit.remainder_exit
        if (
            not rule.enabled
            or state.quantity <= 0
            or state.state != PositionState.PARTIAL_TP_1
            or not state.tp1_done
            or not state.cycle_id
        ):
            return tp1_hits, tp2_hits, profitable_rebuy

        tp1_day = self._tp1_day.get(state.cycle_id)
        if tp1_day is None:
            return tp1_hits, tp2_hits, profitable_rebuy
        elapsed_sessions = state.cycle_holding_days - tp1_day
        if not remainder_exit_due(elapsed_sessions, rule):
            return tp1_hits, tp2_hits, profitable_rebuy

        exit_price = remainder_exit_price(state.average_price, rule)
        if high < exit_price:
            return tp1_hits, tp2_hits, profitable_rebuy

        used_rebuy = state.cycle_used_rebuy
        cycle_id = state.cycle_id
        self._sell(
            state,
            timestamp,
            exit_price,
            state.quantity,
            "REMAINDER_EXIT",
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


def _with_remainder_rule(config, wait_days: int | None, target_pct: Decimal):
    rule = replace(
        config.take_profit.remainder_exit,
        enabled=wait_days is not None,
        wait_trading_days=(
            wait_days
            if wait_days is not None
            else config.take_profit.remainder_exit.wait_trading_days
        ),
        target_from_avg=target_pct,
    )
    return replace(
        config,
        take_profit=replace(config.take_profit, remainder_exit=rule),
    )


def build_candidates(base):
    candidates = {}
    for label, tp2 in (("TP46", "0.06"), ("TP48", "0.08")):
        champion = _with_stage1_guard(_with_entry_score(_with_tp(base, tp2), 55))
        candidates[f"{label}_CHAMPION"] = _with_remainder_rule(
            champion, None, Decimal("0")
        )
        for wait_days in (20, 40, 60):
            for target in (Decimal("0"), Decimal("0.02")):
                name = f"{label}_R{wait_days}_P{int(target * 100):02d}"
                candidates[name] = _with_remainder_rule(champion, wait_days, target)
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

    for name, config in build_candidates(base).items():
        rule = config.take_profit.remainder_exit
        item = {
            "settings": {
                "tp2": float(config.take_profit.tp2_base),
                "entry_score": config.global_.entry_score,
                "remainder_enabled": rule.enabled,
                "wait_days_after_tp1": rule.wait_trading_days if rule.enabled else None,
                "remainder_target_pct": float(rule.target_from_avg),
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
                engine = RemainderExitEngine(config)
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
            "Research comparison using the same remainder-exit due/price functions "
            "as live trading. No stop-loss is used."
        ),
        "",
        "| Candidate | CAGR | MDD | P95 MAE | >40d | Max hold | Open DD | Cycles |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, item in report["candidates"].items():
        metrics = item["segments"]["validation_2021_2024"]["combined"]
        lines.append(
            f"| {name} | {metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% | "
            f"{metrics['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{metrics['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{metrics['max_holding_days_worst_symbol_including_open']}d | "
            f"{metrics['open_price_drawdown_worst_symbol_pct']:.2f}% | "
            f"{metrics['closed_cycles']} |"
        )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
