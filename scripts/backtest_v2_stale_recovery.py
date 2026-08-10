#!/usr/bin/env python3
"""Test non-loss recovery exits for stale cycles before TP1."""

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
from backtest_v2_remainder_exit import RemainderExitEngine

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


class StaleRecoveryEngine(RemainderExitEngine):
    """Exit an old pre-TP1 cycle only after price recovers to a non-loss target."""

    def __init__(
        self,
        config,
        *,
        remainder_wait_days: int,
        remainder_target_pct: Decimal,
        stale_days: int | None,
        stale_target_pct: Decimal,
    ) -> None:
        super().__init__(
            config,
            wait_days=remainder_wait_days,
            target_pct=remainder_target_pct,
        )
        self.stale_days = stale_days
        self.stale_target_pct = stale_target_pct

    def _process_take_profit(self, state, timestamp, high, trades, closed_cycles):
        if (
            self.stale_days is not None
            and state.quantity > 0
            and not state.tp1_done
            and state.cycle_id
            and state.cycle_holding_days >= self.stale_days
        ):
            exit_price = state.average_price * (Decimal("1") + self.stale_target_pct)
            if high >= exit_price:
                cycle_id = state.cycle_id
                used_rebuy = state.cycle_used_rebuy
                self._sell(
                    state,
                    timestamp,
                    exit_price,
                    state.quantity,
                    f"STALE_RECOVERY_{self.stale_days}D_{self.stale_target_pct}",
                    trades,
                )
                closed_cycles.append(
                    {
                        "cycle_id": cycle_id,
                        "start_date": (
                            state.cycle_start_date.isoformat() if state.cycle_start_date else None
                        ),
                        "end_date": timestamp.date().isoformat(),
                        "holding_days": state.cycle_holding_days,
                        "pnl": round(float(state.cycle_cashflows), 2),
                        "mae": state.cycle_mae,
                        "mfe": state.cycle_mfe,
                        "entry_count": state.entry_count,
                        "used_rebuy": used_rebuy,
                    }
                )
                profitable_rebuy = int(used_rebuy and state.cycle_cashflows > 0)
                self._reset_cycle(state)
                return 0, 0, profitable_rebuy
        return super()._process_take_profit(state, timestamp, high, trades, closed_cycles)


def _champion(base, tp2: str):
    return _with_stage1_guard(_with_entry_score(_with_tp(base, tp2), 55))


def build_candidates(base):
    roots = {
        "TP46_R20_P02": (_champion(base, "0.06"), 20, Decimal("0.02")),
        "TP48_R40_P00": (_champion(base, "0.08"), 40, Decimal("0.00")),
    }
    candidates = {}
    for root_name, (config, remainder_days, remainder_target) in roots.items():
        candidates[f"{root_name}_S0"] = (
            config,
            remainder_days,
            remainder_target,
            None,
            Decimal("0"),
        )
        for stale_days in (60, 90, 120, 160):
            for target in (Decimal("0"), Decimal("0.01"), Decimal("0.02")):
                name = f"{root_name}_S{stale_days}_P{int(target * 100):02d}"
                candidates[name] = (
                    config,
                    remainder_days,
                    remainder_target,
                    stale_days,
                    target,
                )
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_stale_recovery.json",
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

    for name, spec in build_candidates(base).items():
        config, remainder_days, remainder_target, stale_days, stale_target = spec
        item = {
            "settings": {
                "tp2": float(config.take_profit.tp2_base),
                "remainder_wait_days": remainder_days,
                "remainder_target_pct": float(remainder_target),
                "stale_days": stale_days,
                "stale_target_pct": float(stale_target),
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
                engine = StaleRecoveryEngine(
                    config,
                    remainder_wait_days=remainder_days,
                    remainder_target_pct=remainder_target,
                    stale_days=stale_days,
                    stale_target_pct=stale_target,
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
        "# JDSS 2.0 Stale-Cycle Recovery Search",
        "",
        "No stop-loss: an old pre-TP1 cycle exits only after recovery to average cost or a profit target.",
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
            f"{m['open_price_drawdown_worst_symbol_pct']:.2f}% | {m['closed_cycles']} |"
        )
    md_path = args.output.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
