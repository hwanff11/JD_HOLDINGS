#!/usr/bin/env python3
"""Study S1/S2/S3 score floors for JDSS V3.1.1 without changing production."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.indicators import calculate_indicators
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
STAGE_VALUES = (45, 50, 55, 60, 65)
BASELINE = (55, 55, 55)
DEVELOPMENT = {
    "train": ("2011-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2022-12-30"),
}
FINAL = {
    "oos": ("2023-01-01", None),
    "recent": ("2022-01-01", None),
    "full": ("2011-01-01", None),
}


def _config_with_thresholds(
    config: StrategyConfig,
    s1: int,
    s2: int,
    s3: int,
) -> StrategyConfig:
    global_config = replace(config.global_, entry_score=s1)
    score_by_stage = {"stage2": s2, "stage3": s3}
    stages = {
        name: replace(rule, min_score=score_by_stage.get(name, rule.min_score))
        for name, rule in config.additional_entry.stages.items()
    }
    return replace(
        config,
        global_=global_config,
        additional_entry=replace(config.additional_entry, stages=stages),
    )


def _prepare_frames(
    config: StrategyConfig,
    end: str,
    refresh: bool,
) -> dict[str, pd.DataFrame]:
    source = YFinanceDataSource(ROOT / "data" / "cache")
    raw = {
        symbol: source.daily(symbol, "2009-11-27", end, refresh=refresh)
        for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    }
    return {symbol: calculate_indicators(frame, config) for symbol, frame in raw.items()}


def _stage_reach(result) -> dict[str, int]:
    entry_counts = [int(cycle.get("entry_count", 0)) for cycle in result.closed_cycles]
    open_entry_count = int(result.open_position.get("entry_count", 0))
    if int(result.open_position.get("quantity", 0)) > 0 and open_entry_count > 0:
        entry_counts.append(open_entry_count)
    return {
        "cycles_stage1": len(entry_counts),
        "cycles_stage2": sum(count >= 2 for count in entry_counts),
        "cycles_stage3": sum(count >= 3 for count in entry_counts),
    }


def _evaluate(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    thresholds: tuple[int, int, int],
    start: str,
    end: str,
    slippage: float,
) -> dict[str, Any]:
    scenario = _config_with_thresholds(config, *thresholds)
    boosters = {}
    stage_totals = {"cycles_stage1": 0, "cycles_stage2": 0, "cycles_stage3": 0}
    symbol_details: dict[str, dict[str, Any]] = {}

    for symbol in scenario.enabled_symbols:
        sector_data = None
        if symbol == "SOXL":
            sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
        result = StrategyBacktestEngine(scenario).run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start=start,
            end=end,
            slippage=slippage,
            indicators_precomputed=True,
            sector_data=sector_data,
        )
        boosters[symbol] = result
        reach = _stage_reach(result)
        for key, value in reach.items():
            stage_totals[key] += value
        symbol_details[symbol] = {
            "signals": int(result.metrics["signals"]),
            "executed_entries": int(result.metrics["executed_entries"]),
            "closed_cycles": int(result.metrics["closed_cycles"]),
            **reach,
        }

    portfolio = PortfolioBacktestEngine(scenario).run(
        {
            "TQQQ": frames["TQQQ"],
            "SOXL": frames["SOXL"],
            "QQQ": frames["QQQ"],
            "SOXX": frames["SOXX"],
        },
        boosters,
        start=start,
        end=end,
        slippage=slippage,
    )
    metrics = dict(portfolio.metrics)
    metrics["booster_signals"] = sum(int(result.metrics["signals"]) for result in boosters.values())
    metrics["booster_entries"] = sum(int(result.metrics["executed_entries"]) for result in boosters.values())
    metrics["booster_closed_cycles"] = sum(
        int(result.metrics["closed_cycles"]) for result in boosters.values()
    )
    metrics.update(stage_totals)
    metrics["symbol_details"] = symbol_details
    return metrics


def _utility(metrics: dict[str, Any]) -> float:
    return float(metrics["cagr_pct"]) - 0.25 * abs(float(metrics["mdd_pct"])) + 2.0 * float(metrics["sharpe"])


def _objective(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> float:
    deltas = [_utility(candidate[split]) - _utility(baseline[split]) for split in DEVELOPMENT]
    return min(deltas) + 0.30 * (sum(deltas) / len(deltas))


def _eligible(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> bool:
    for split in DEVELOPMENT:
        current = candidate[split]
        reference = baseline[split]
        if float(current["total_return_pct"]) <= 0:
            return False
        if int(current["booster_entries"]) < 0.70 * int(reference["booster_entries"]):
            return False
        if abs(float(current["mdd_pct"])) > abs(float(reference["mdd_pct"])) + 3.0:
            return False
        if float(current["average_exposure_pct"]) > float(reference["average_exposure_pct"]) + 5.0:
            return False
    return True


def _dominates(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> bool:
    for split in DEVELOPMENT:
        current = candidate[split]
        reference = baseline[split]
        if float(current["cagr_pct"]) < float(reference["cagr_pct"]):
            return False
        if abs(float(current["mdd_pct"])) > abs(float(reference["mdd_pct"])):
            return False
        if float(current["sharpe"]) < float(reference["sharpe"]):
            return False
    return True


def _family(thresholds: tuple[int, int, int]) -> str:
    s1, s2, s3 = thresholds
    if s1 <= s2 <= s3 and len({s1, s2, s3}) > 1:
        return "stricter_deeper"
    if s1 >= s2 >= s3 and len({s1, s2, s3}) > 1:
        return "looser_deeper"
    if s1 == s2 == s3:
        return "flat"
    return "mixed"


def _compact(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "total_return_pct",
        "cagr_pct",
        "mdd_pct",
        "sharpe",
        "sortino",
        "average_exposure_pct",
        "booster_signals",
        "booster_entries",
        "booster_closed_cycles",
        "cycles_stage1",
        "cycles_stage2",
        "cycles_stage3",
        "component_fills",
        "maximum_invested_cost",
        "annual_returns_pct",
        "symbol_details",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def _row(
    thresholds: tuple[int, int, int],
    development: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    s1, s2, s3 = thresholds
    row: dict[str, Any] = {
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "family": _family(thresholds),
        "objective": round(_objective(development, baseline), 6),
        "eligible": _eligible(development, baseline),
        "dominates_development": _dominates(development, baseline),
    }
    for split, metrics in development.items():
        for key in (
            "cagr_pct",
            "mdd_pct",
            "sharpe",
            "sortino",
            "average_exposure_pct",
            "booster_entries",
            "cycles_stage2",
            "cycles_stage3",
        ):
            row[f"{split}_{key}"] = metrics[key]
    return row


def _spec(row: dict[str, Any]) -> tuple[int, int, int]:
    return int(row["s1"]), int(row["s2"]), int(row["s3"])


def _add_locked(
    locked: dict[str, tuple[int, int, int]],
    label: str,
    thresholds: tuple[int, int, int],
) -> None:
    if thresholds not in locked.values():
        locked[label] = thresholds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/stage_score_thresholds.json",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "reports/stage_score_thresholds_grid.csv",
    )
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = _prepare_frames(config, end, args.refresh)

    baseline_dev = {
        split: _evaluate(config, frames, BASELINE, start, split_end, args.slippage)
        for split, (start, split_end) in DEVELOPMENT.items()
    }

    rows: list[dict[str, Any]] = []
    grid = list(product(STAGE_VALUES, repeat=3))
    for index, thresholds in enumerate(grid, start=1):
        development = {
            split: _evaluate(config, frames, thresholds, start, split_end, args.slippage)
            for split, (start, split_end) in DEVELOPMENT.items()
        }
        rows.append(_row(thresholds, development, baseline_dev))
        print(
            f"evaluated {index}/{len(grid)}: S1/S2/S3={thresholds[0]}/{thresholds[1]}/{thresholds[2]}",
            flush=True,
        )

    frame = pd.DataFrame(rows).sort_values(
        ["eligible", "objective"],
        ascending=[False, False],
    )
    eligible = frame[frame["eligible"]]
    candidate_pool = eligible if not eligible.empty else frame

    locked: dict[str, tuple[int, int, int]] = {"baseline_55_55_55": BASELINE}
    for rank, (_, row) in enumerate(candidate_pool.head(5).iterrows(), start=1):
        _add_locked(locked, f"top_{rank}", _spec(row.to_dict()))

    for family in ("stricter_deeper", "looser_deeper", "flat", "mixed"):
        subset = candidate_pool[candidate_pool["family"] == family]
        if not subset.empty:
            _add_locked(locked, f"best_{family}", _spec(subset.iloc[0].to_dict()))

    s1_55 = candidate_pool[candidate_pool["s1"] == 55]
    if not s1_55.empty:
        _add_locked(locked, "best_s1_55", _spec(s1_55.iloc[0].to_dict()))

    final_periods: dict[str, dict[str, dict[str, Any]]] = {}
    for split, (start, split_end) in FINAL.items():
        resolved_end = split_end or end
        final_periods[split] = {
            label: _compact(_evaluate(config, frames, thresholds, start, resolved_end, args.slippage))
            for label, thresholds in locked.items()
        }

    report = {
        "strategy_version": config.version,
        "research_end": end,
        "method": {
            "stage_values": list(STAGE_VALUES),
            "candidate_count": len(grid),
            "baseline": list(BASELINE),
            "selection": "Train 2011-2018 + validation 2019-2022 only; OOS opened after locking.",
            "fees": float(config.global_.buy_fee),
            "slippage": args.slippage,
        },
        "baseline_development": {split: _compact(metrics) for split, metrics in baseline_dev.items()},
        "development_top10": frame.head(10).to_dict(orient="records"),
        "development_dominators": frame[frame["dominates_development"]].to_dict(orient="records"),
        "locked_specs": {label: list(spec) for label, spec in locked.items()},
        "final_periods": final_periods,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    frame.to_csv(args.csv, index=False)

    print("\n=== DEVELOPMENT TOP 10 ===")
    print(
        frame[
            [
                "s1",
                "s2",
                "s3",
                "family",
                "objective",
                "train_cagr_pct",
                "train_mdd_pct",
                "validation_cagr_pct",
                "validation_mdd_pct",
            ]
        ]
        .head(10)
        .to_string(index=False)
    )
    print("\n=== LOCKED SPECS ===")
    for label, thresholds in locked.items():
        print(label, "/".join(str(value) for value in thresholds))
    print("\n=== OOS ===")
    for label, metrics in final_periods["oos"].items():
        spec = locked[label]
        print(
            label,
            f"{spec[0]}/{spec[1]}/{spec[2]}",
            f"CAGR={metrics['cagr_pct']:+.2f}%",
            f"MDD={metrics['mdd_pct']:.2f}%",
            f"Sharpe={metrics['sharpe']:.3f}",
            f"S2cycles={metrics['cycles_stage2']}",
            f"S3cycles={metrics['cycles_stage3']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
