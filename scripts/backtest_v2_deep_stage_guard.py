#!/usr/bin/env python3
"""Test deep additional-entry guards after the post-TP1 exit research."""

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


class DeepStageGuardEngine(RemainderExitEngine):
    """Research engine blocking deep averaging while the ETF trend is structurally weak."""

    def __init__(
        self,
        config,
        *,
        wait_days: int,
        target_pct: Decimal,
        blocked_stages: tuple[int, ...],
    ) -> None:
        super().__init__(config, wait_days=wait_days, target_pct=target_pct)
        self.blocked_stages = set(blocked_stages)

    def _execute_pending(self, pending, state, timestamp, next_open, slippage, trades):
        decision = pending.decision
        if (
            decision.action == DecisionType.ADD_ENTRY_CANDIDATE
            and int(decision.target_stage or 0) in self.blocked_stages
            and float(pending.snapshot.close) < pending.snapshot.ema60
            and pending.snapshot.ema20 < pending.snapshot.ema60
        ):
            return False, "RESEARCH_DEEP_STAGE_TREND_GUARD"
        return super()._execute_pending(
            pending,
            state,
            timestamp,
            next_open,
            slippage,
            trades,
        )


def _champion(base, tp2: str):
    return _with_stage1_guard(_with_entry_score(_with_tp(base, tp2), 55))


def build_candidates(base):
    roots = {
        "TP46_R20_P02": (_champion(base, "0.06"), 20, Decimal("0.02")),
        "TP48_R40_P00": (_champion(base, "0.08"), 40, Decimal("0.00")),
    }
    candidates = {}
    for root_name, (config, wait_days, target_pct) in roots.items():
        candidates[f"{root_name}_D0"] = (config, wait_days, target_pct, ())
        candidates[f"{root_name}_D4"] = (config, wait_days, target_pct, (4,))
        candidates[f"{root_name}_D34"] = (config, wait_days, target_pct, (3, 4))
        candidates[f"{root_name}_D234"] = (config, wait_days, target_pct, (2, 3, 4))
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_deep_stage_guard.json",
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

    for name, (config, wait_days, target_pct, blocked_stages) in build_candidates(base).items():
        item = {
            "settings": {
                "tp2": float(config.take_profit.tp2_base),
                "entry_score": config.global_.entry_score,
                "wait_days_after_tp1": wait_days,
                "remainder_target_pct": float(target_pct),
                "deep_stage_blocked": list(blocked_stages),
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
                engine = DeepStageGuardEngine(
                    config,
                    wait_days=wait_days,
                    target_pct=target_pct,
                    blocked_stages=blocked_stages,
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
        "# JDSS 2.0 Deep-Stage Trend Guard Search",
        "",
        (
            "After the selected post-TP1 exit rule, block configured averaging "
            "stages only when the traded ETF is below EMA60 and EMA20 is below EMA60."
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
