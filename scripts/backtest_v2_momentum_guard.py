#!/usr/bin/env python3
"""Test momentum-aware additional-entry guards for JDSS 2.0 finalists."""

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
from jd_holdings.core.enums import DecisionType
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent


class MomentumGuardEngine(RemainderExitEngine):
    """Block deep averaging only during accelerating downside momentum."""

    def __init__(
        self,
        config,
        *,
        wait_days: int,
        target_pct: Decimal,
        mode: str,
    ) -> None:
        super().__init__(config, wait_days=wait_days, target_pct=target_pct)
        self.mode = mode

    def _blocked(self, pending) -> bool:
        snapshot = pending.snapshot
        stage = int(pending.decision.target_stage or 0)
        if stage < 3:
            return False
        price = float(snapshot.close)
        ema20 = float(snapshot.ema20)
        ema60 = float(snapshot.ema60)
        weak_trend = price < ema60 and ema20 < ema60
        severe_trend = price < ema20 < ema60
        score = int(getattr(snapshot, "score", 0) or 0)
        if self.mode == "weak_score90":
            return weak_trend and score < 90
        if self.mode == "severe_score90":
            return severe_trend and score < 90
        if self.mode == "stage4_weak_score90":
            return stage == 4 and weak_trend and score < 90
        return False

    def _execute_pending(self, pending, state, timestamp, next_open, slippage, trades):
        if (
            pending.decision.action == DecisionType.ADD_ENTRY_CANDIDATE
            and self._blocked(pending)
        ):
            return False, "RESEARCH_MOMENTUM_AVERAGING_GUARD"
        return super()._execute_pending(
            pending, state, timestamp, next_open, slippage, trades
        )


def _champion(base, tp2: str):
    return _with_stage1_guard(_with_entry_score(_with_tp(base, tp2), 55))


def build_candidates(base):
    roots = {
        "TP46_R20_P02": (_champion(base, "0.06"), 20, Decimal("0.02")),
        "TP48_R40_P00": (_champion(base, "0.08"), 40, Decimal("0.00")),
    }
    modes = ("none", "weak_score90", "severe_score90", "stage4_weak_score90")
    return {
        f"{root}_{mode}": (config, wait_days, target_pct, mode)
        for root, (config, wait_days, target_pct) in roots.items()
        for mode in modes
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "v2_momentum_guard.json"
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
    for name, (config, wait_days, target_pct, mode) in build_candidates(base).items():
        item = {"mode": mode, "segments": {}}
        for segment_name, (start, end) in segments.items():
            results = {}
            for symbol in SYMBOLS:
                sector_data = (
                    {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None
                )
                engine = MomentumGuardEngine(
                    config,
                    wait_days=wait_days,
                    target_pct=target_pct,
                    mode=mode,
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
                "symbols": {symbol: result.metrics for symbol, result in results.items()},
            }
        report["candidates"][name] = item
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# JDSS 2.0 Momentum-Aware Averaging Guard",
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
