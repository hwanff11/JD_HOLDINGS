#!/usr/bin/env python3
# ruff: noqa: E402, E501
"""Grid search for high-return & high-turnover swing strategy parameters."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".venv" / "lib" / "python3.12" / "site-packages"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from typing import Any

import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import AdditionalEntryConfig, StageRule, load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource


def _combined_metrics(results: dict[str, BacktestResult], annualization_days: int) -> dict[str, Any]:
    equity = pd.concat(
        [result.equity_curve.rename(symbol) for symbol, result in results.items()],
        axis=1,
        join="inner",
    ).sum(axis=1)
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    elapsed_years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    total_return = final / initial - 1
    cagr = (final / initial) ** (1 / elapsed_years) - 1
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    yearly_returns = {
        str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
        for year, group in equity.groupby(equity.index.year)
    }
    
    total_closed = sum(int(result.metrics["closed_cycles"]) for result in results.values())
    total_wins = sum(
        int(round(int(result.metrics["closed_cycles"]) * float(result.metrics["win_rate_pct"]) / 100))
        for result in results.values()
    )
    win_rate = (total_wins / total_closed * 100) if total_closed > 0 else 0.0

    return {
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "win_rate_pct": round(win_rate, 1),
        "closed_cycles": total_closed,
        "signals": sum(int(result.metrics["signals"]) for result in results.values()),
        "executed_entries": sum(
            int(result.metrics["executed_entries"]) for result in results.values()
        ),
        "annual_returns_pct": yearly_returns,
    }

SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


def _candidate(
    base,
    *,
    entry: int,
    chase: str,
    weights: tuple[str, str, str, str],
    drops: tuple[str, str, str],
    add_scores: tuple[int, int, int],
    tp: tuple[str, str],
    min_reversal: int = 5,
):
    decimal_weights = tuple(Decimal(value) for value in weights)
    cumulative = tuple(sum(decimal_weights[:index]) for index in range(1, 5))
    return replace(
        base,
        global_=replace(
            base.global_,
            entry_score=entry,
            minimum_reversal_score=min_reversal,
            entry_max_chase_pct=Decimal(chase),
        ),
        position=replace(
            base.position,
            stage_weights=decimal_weights,
            cumulative_weights=cumulative,
        ),
        additional_entry=AdditionalEntryConfig(
            anchor=base.additional_entry.anchor,
            max_stage_per_day=1,
            stages={
                stage: StageRule(Decimal(drop), score)
                for stage, drop, score in zip((2, 3, 4), drops, add_scores, strict=True)
            },
        ),
        take_profit=replace(
            base.take_profit,
            tp1_base=Decimal(tp[0]),
            tp2_base=Decimal(tp[1]),
        ),
    )


def build_candidates(base):
    cands = {}
    
    # 1. Baseline
    cands["v2.0_baseline"] = base

    # 2. Aggressive Entry (Lower Cutoff, Higher Trading Frequency)
    # Entry 52, 55, 58
    cands["aggr_52_tp36"] = _candidate(
        base, entry=52, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.02", "0.04", "0.07"), add_scores=(55, 58, 60), tp=("0.03", "0.06")
    )
    cands["aggr_55_tp48"] = _candidate(
        base, entry=55, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.02", "0.04", "0.07"), add_scores=(58, 60, 62), tp=("0.04", "0.08")
    )
    cands["aggr_58_tp510"] = _candidate(
        base, entry=58, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.025", "0.05", "0.08"), add_scores=(60, 62, 64), tp=("0.05", "0.10")
    )

    # 3. High TP (Take Profit 4/8%, 5/10%, 6/12%)
    cands["tp_4_8_entry60"] = _candidate(
        base, entry=60, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.02", "0.04", "0.07"), add_scores=(62, 64, 66), tp=("0.04", "0.08")
    )
    cands["tp_5_10_entry60"] = _candidate(
        base, entry=60, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.02", "0.04", "0.07"), add_scores=(62, 64, 66), tp=("0.05", "0.10")
    )
    cands["tp_6_12_entry60"] = _candidate(
        base, entry=60, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.02", "0.04", "0.07"), add_scores=(62, 64, 66), tp=("0.06", "0.12")
    )

    # 4. Tight Drops (-1.5%, -3%, -5% for faster staging)
    cands["tight_drop_entry55"] = _candidate(
        base, entry=55, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.015", "0.03", "0.05"), add_scores=(58, 60, 62), tp=("0.04", "0.08")
    )
    cands["tight_drop_entry58"] = _candidate(
        base, entry=58, chase="0.04", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.015", "0.03", "0.05"), add_scores=(60, 62, 64), tp=("0.04", "0.08")
    )

    # 5. Equal Stage Weights (25/25/25/25)
    cands["equal_weights_entry58"] = _candidate(
        base, entry=58, chase="0.04", weights=("0.25", "0.25", "0.25", "0.25"),
        drops=("0.02", "0.04", "0.07"), add_scores=(60, 62, 64), tp=("0.04", "0.08")
    )
    cands["equal_weights_entry55"] = _candidate(
        base, entry=55, chase="0.04", weights=("0.25", "0.25", "0.25", "0.25"),
        drops=("0.02", "0.04", "0.07"), add_scores=(58, 60, 62), tp=("0.04", "0.08")
    )

    # 6. Ultra-High Frequency (Entry 50)
    cands["ultra_hf_50_tp36"] = _candidate(
        base, entry=50, chase="0.05", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.015", "0.03", "0.05"), add_scores=(52, 54, 56), tp=("0.03", "0.06")
    )
    cands["ultra_hf_50_tp48"] = _candidate(
        base, entry=50, chase="0.05", weights=("0.40", "0.30", "0.20", "0.10"),
        drops=("0.02", "0.04", "0.07"), add_scores=(52, 54, 56), tp=("0.04", "0.08")
    )

    return cands


def _settings(config):
    return {
        "entry_score": config.global_.entry_score,
        "minimum_reversal_score": config.global_.minimum_reversal_score,
        "entry_max_chase_pct": float(config.global_.entry_max_chase_pct),
        "stage_weights": [float(value) for value in config.position.stage_weights],
        "stage_drops": [
            float(config.additional_entry.stages[stage].min_drop_from_anchor)
            for stage in (2, 3, 4)
        ],
        "additional_entry_scores": [
            config.additional_entry.stages[stage].min_score for stage in (2, 3, 4)
        ],
        "take_profit": [
            float(config.take_profit.tp1_base),
            float(config.take_profit.tp2_base),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-04")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "high_return_exploration.json")
    args = parser.parse_args()

    base = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)).isoformat()
    
    print("📥 Loading market data...")
    frames = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }

    segments = {
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "recent_2025": ("2025-01-01", "2025-12-31"),
        "recent_2026_ytd": ("2026-01-01", "2026-07-31"),
        "full_history": ("2011-01-01", args.end),
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "slippage": 0.001,
        "candidates": {},
    }

    candidates = build_candidates(base)
    print(f"🚀 Running Grid Search across {len(candidates)} strategy candidates...\n")

    summary_list = []

    for name, config in candidates.items():
        candidate = {"settings": _settings(config), "segments": {}}
        for segment, (start, end) in segments.items():
            results = {
                symbol: BacktestEngine(config).run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=0.001,
                    sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None,
                )
                for symbol in SYMBOLS
            }
            candidate["segments"][segment] = {
                "combined": _combined_metrics(results, config.backtest.annualization_days),
                "symbols": {
                    symbol: {
                        "metrics": result.metrics,
                        "open_position": result.open_position,
                    }
                    for symbol, result in results.items()
                },
            }

        val_metrics = candidate["segments"]["validation_2021_2024"]["combined"]
        full_metrics = candidate["segments"]["full_history"]["combined"]

        summary_list.append({
            "name": name,
            "val_return": val_metrics["total_return_pct"],
            "val_mdd": val_metrics["mdd_pct"],
            "val_cycles": val_metrics["closed_cycles"],
            "val_win_rate": val_metrics["win_rate_pct"],
            "full_return": full_metrics["total_return_pct"],
            "full_cagr": full_metrics["cagr_pct"],
            "full_mdd": full_metrics["mdd_pct"],
            "full_cycles": full_metrics["closed_cycles"],
        })

        report["candidates"][name] = candidate

    # Sort summary by 2021-2024 Validation Return descending
    summary_list.sort(key=lambda item: item["val_return"], reverse=True)

    print("\n🏆 ===== Grid Search Result Summary (Sorted by 2021-2024 Return) =====")
    print(f"{'Candidate':<24} | {'2021-24 Ret':<11} | {'2021-24 MDD':<11} | {'Cycles':<7} | {'Full Ret':<10} | {'Full MDD':<10}")
    print("-" * 85)
    for s in summary_list:
        print(
            f"{s['name']:<24} | {s['val_return']:>+10.2f}% | {s['val_mdd']:>10.2f}% | {s['val_cycles']:>7d} | {s['full_return']:>+9.2f}% | {s['full_mdd']:>9.2f}%"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 Saved full report to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
