#!/usr/bin/env python3
"""Test time-decaying TP2 targets after TP1 without introducing a stop loss."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine, BacktestResult, _SimulationState
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.take_profit import ceil_to_tick
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


@dataclass(frozen=True)
class CandidateSpec:
    config: StrategyConfig
    decay_after_days: int | None = None
    decayed_tp2_rate: Decimal | None = None


class DecayTPBacktestEngine(BacktestEngine):
    """Research-only engine that compresses TP2 after TP1 has remained open."""

    def __init__(
        self,
        config: StrategyConfig,
        *,
        decay_after_days: int | None = None,
        decayed_tp2_rate: Decimal | None = None,
    ) -> None:
        super().__init__(config)
        self.decay_after_days = decay_after_days
        self.decayed_tp2_rate = decayed_tp2_rate
        self._tp1_age: dict[str, int] = {}

    def _process_take_profit(
        self,
        state: _SimulationState,
        timestamp: pd.Timestamp,
        high: Decimal,
        trades: list[dict[str, Any]],
        closed_cycles: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        cycle_id = state.cycle_id
        if (
            cycle_id
            and state.tp1_done
            and state.tp_plan is not None
            and self.decay_after_days is not None
            and self.decayed_tp2_rate is not None
        ):
            age = self._tp1_age.get(cycle_id, 0) + 1
            self._tp1_age[cycle_id] = age
            if age >= self.decay_after_days:
                decayed_price = ceil_to_tick(
                    state.tp_plan.average_price * (Decimal("1") + self.decayed_tp2_rate)
                )
                if decayed_price < state.tp_plan.tp2_price:
                    state.tp_plan = replace(
                        state.tp_plan,
                        tp2_rate=self.decayed_tp2_rate,
                        tp2_price=decayed_price,
                    )

        tp1_hits, tp2_hits, profitable_rebuy = super()._process_take_profit(
            state,
            timestamp,
            high,
            trades,
            closed_cycles,
        )
        if tp1_hits and cycle_id:
            self._tp1_age[cycle_id] = 0
        if cycle_id and (tp2_hits or state.quantity == 0):
            self._tp1_age.pop(cycle_id, None)
        return tp1_hits, tp2_hits, profitable_rebuy


def _with_tp(base: StrategyConfig, tp1: str, tp2: str) -> StrategyConfig:
    return replace(
        base,
        take_profit=replace(
            base.take_profit,
            tp1_base=Decimal(tp1),
            tp2_base=Decimal(tp2),
        ),
    )


def build_candidates(base: StrategyConfig) -> dict[str, CandidateSpec]:
    return {
        "A_baseline_tp48": CandidateSpec(base),
        "I_static_tp46": CandidateSpec(_with_tp(base, "0.04", "0.06")),
        "S_tp48_decay5_to4": CandidateSpec(base, 5, Decimal("0.04")),
        "T_tp48_decay10_to4": CandidateSpec(base, 10, Decimal("0.04")),
        "U_tp48_decay10_to5": CandidateSpec(base, 10, Decimal("0.05")),
    }


def _holding_days_including_open(result: BacktestResult) -> list[int]:
    days = [int(cycle["holding_days"]) for cycle in result.closed_cycles]
    if int(result.open_position["quantity"]) > 0:
        days.append(int(result.open_position["holding_days"]))
    return days


def _combined_metrics(results: dict[str, BacktestResult], annualization_days: int) -> dict[str, Any]:
    equity = pd.concat(
        [result.equity_curve.rename(symbol) for symbol, result in results.items()],
        axis=1,
        join="inner",
    ).sum(axis=1)
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    cagr = (final / initial) ** (1 / years) - 1
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    all_days = {
        symbol: _holding_days_including_open(result) for symbol, result in results.items()
    }
    flattened = [day for days in all_days.values() for day in days]
    lockup_worst = max(
        (
            sum(day > 40 for day in days) / len(days) * 100
            if days
            else 0.0
        )
        for days in all_days.values()
    )
    open_drawdown = []
    for result in results.values():
        position = result.open_position
        if int(position["quantity"]) <= 0 or float(position["average_price"]) <= 0:
            open_drawdown.append(0.0)
        else:
            open_drawdown.append(
                (float(position["market_price"]) / float(position["average_price"]) - 1) * 100
            )
    return {
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "closed_cycles": sum(int(result.metrics["closed_cycles"]) for result in results.values()),
        "avg_holding_days_including_open": round(
            sum(flattened) / len(flattened) if flattened else 0.0,
            2,
        ),
        "max_holding_days_including_open": max(flattened, default=0),
        "lockup_over_40_days_worst_symbol_pct": round(lockup_worst, 2),
        "mae_p95_worst_symbol_pct": min(
            float(result.metrics["mae_p95_pct"]) for result in results.values()
        ),
        "open_price_drawdown_worst_symbol_pct": round(min(open_drawdown), 2),
        "capital_utilization_avg_pct": round(
            sum(float(result.metrics["average_capital_utilization_pct"]) for result in results.values())
            / len(results),
            2,
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 2.0 TP2 Decay Research",
        "",
        "TP1 remains +4%. Decay candidates lower only the remaining TP2 target after TP1.",
        "No stop loss or forced loss exit is introduced.",
        "",
        "## 2021-2024 validation",
        "",
        (
            "| Candidate | CAGR | MDD | P95 MAE | >40d lockup | Max hold | "
            "Open DD | Cycles | Avg hold |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = []
    for name, candidate in report["candidates"].items():
        metrics = candidate["segments"]["validation_2021_2024"]["combined"]
        rows.append((float(metrics["cagr_pct"]), name, metrics))
    for _, name, metrics in sorted(rows, reverse=True):
        lines.append(
            f"| {name} | {metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% | "
            f"{metrics['mae_p95_worst_symbol_pct']:.2f}% | "
            f"{metrics['lockup_over_40_days_worst_symbol_pct']:.2f}% | "
            f"{metrics['max_holding_days_including_open']}d | "
            f"{metrics['open_price_drawdown_worst_symbol_pct']:.2f}% | "
            f"{metrics['closed_cycles']} | {metrics['avg_holding_days_including_open']:.1f}d |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_tp2_decay.json",
    )
    args = parser.parse_args()

    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup, args.end, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    segments = {
        "development_2011_2020": ("2011-01-01", "2020-12-31"),
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "recent_2025_present": ("2025-01-01", args.end),
        "full_history": ("2011-01-01", args.end),
    }

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "candidates": {}}
    for name, spec in build_candidates(base).items():
        candidate: dict[str, Any] = {
            "settings": {
                "tp": [
                    float(spec.config.take_profit.tp1_base),
                    float(spec.config.take_profit.tp2_base),
                ],
                "decay_after_days": spec.decay_after_days,
                "decayed_tp2_rate": (
                    float(spec.decayed_tp2_rate) if spec.decayed_tp2_rate is not None else None
                ),
            },
            "segments": {},
        }
        for segment_name, (start, end) in segments.items():
            results: dict[str, BacktestResult] = {}
            for symbol in SYMBOLS:
                engine = DecayTPBacktestEngine(
                    spec.config,
                    decay_after_days=spec.decay_after_days,
                    decayed_tp2_rate=spec.decayed_tp2_rate,
                )
                results[symbol] = engine.run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=base.backtest.default_slippage,
                    sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None,
                )
            candidate["segments"][segment_name] = {
                "combined": _combined_metrics(results, spec.config.backtest.annualization_days),
                "symbols": {symbol: result.metrics for symbol, result in results.items()},
            }
        report["candidates"][name] = candidate

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = args.output.with_suffix(".md")
    summary.write_text(_markdown(report), encoding="utf-8")
    print(summary.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
