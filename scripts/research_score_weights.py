#!/usr/bin/env python3
"""Research JDSS V3.1.1 component maximum weights without touching production logic.

The production score first applies its existing nonlinear calibration. This study then
rescales each calibrated component to a candidate maximum while keeping the total
maximum at 100 and the entry threshold at 55. Candidate selection uses only
2011-2018 train and 2019-2022 validation data. One locked candidate is then opened
once on the 2023+ holdout and reported on full/recent periods.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import jd_holdings.backtest.engine as backtest_engine_module
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.indicators import calculate_indicators
from jd_holdings.core.models import ScoreResult
from jd_holdings.core.scoring import calculate_grade, calculate_score as production_calculate_score
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
BASE_WEIGHTS = {
    "regime": 25,
    "oversold": 40,
    "reversal": 20,
    "volume": 10,
    "atr": 5,
}
ACTIVE_WEIGHTS = dict(BASE_WEIGHTS)
SPLITS = {
    "train": ("2011-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2022-12-30"),
    "oos": ("2023-01-01", None),
    "recent": ("2022-01-01", None),
    "full": ("2011-01-01", None),
}


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def _rescale(score: int, source_maximum: int, target_maximum: int) -> int:
    if target_maximum <= 0 or score <= 0:
        return 0
    if score >= source_maximum:
        return target_maximum
    return min(target_maximum, _round_half_up(target_maximum * score / source_maximum))


def _research_calculate_score(snapshot, regime, config) -> ScoreResult:
    """Preserve production calibration, then change only component maximum weights."""
    base = production_calculate_score(snapshot, regime, config)
    values = {
        component: _rescale(
            int(getattr(base, f"{component}_score")),
            BASE_WEIGHTS[component],
            ACTIVE_WEIGHTS[component],
        )
        for component in BASE_WEIGHTS
    }
    total = min(100, max(0, sum(values.values())))
    return ScoreResult(
        total=total,
        grade=calculate_grade(total, config),
        regime=base.regime,
        regime_score=values["regime"],
        oversold_score=values["oversold"],
        reversal_score=values["reversal"],
        volume_score=values["volume"],
        atr_score=values["atr"],
        raw_regime_score=base.raw_regime_score,
        raw_oversold_score=base.raw_oversold_score,
        raw_reversal_score=base.raw_reversal_score,
        raw_volume_score=base.raw_volume_score,
        raw_atr_score=base.raw_atr_score,
    )


def _candidate_grid() -> list[dict[str, int]]:
    combinations = itertools.product(
        range(15, 36, 5),  # regime
        range(25, 46, 5),  # oversold
        range(10, 31, 5),  # reversal
        range(5, 21, 5),   # volume
        range(0, 16, 5),   # ATR
    )
    candidates = []
    for regime, oversold, reversal, volume, atr in combinations:
        if regime + oversold + reversal + volume + atr != 100:
            continue
        candidates.append(
            {
                "regime": regime,
                "oversold": oversold,
                "reversal": reversal,
                "volume": volume,
                "atr": atr,
            }
        )
    candidates.sort(
        key=lambda item: (
            sum(abs(item[key] - BASE_WEIGHTS[key]) for key in BASE_WEIGHTS),
            tuple(item[key] for key in BASE_WEIGHTS),
        )
    )
    return candidates


def _prepare_frames(config, end: str, refresh: bool) -> dict[str, pd.DataFrame]:
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = "2009-11-27"
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    raw = {
        symbol: source.daily(symbol, warmup_start, end, refresh=refresh)
        for symbol in symbols
    }
    return {symbol: calculate_indicators(frame, config) for symbol, frame in raw.items()}


def _evaluate(
    config,
    frames: dict[str, pd.DataFrame],
    weights: dict[str, int],
    start: str,
    end: str,
    slippage: float,
) -> dict[str, Any]:
    ACTIVE_WEIGHTS.clear()
    ACTIVE_WEIGHTS.update(weights)
    booster_results = {}
    for symbol in config.enabled_symbols:
        sector_data = None
        if symbol == "SOXL":
            sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
        booster_results[symbol] = StrategyBacktestEngine(config).run(
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
    portfolio_frames = {
        "TQQQ": frames["TQQQ"],
        "SOXL": frames["SOXL"],
        "QQQ": frames["QQQ"],
        "SOXX": frames["SOXX"],
    }
    portfolio = PortfolioBacktestEngine(config).run(
        portfolio_frames,
        booster_results,
        start=start,
        end=end,
        slippage=slippage,
    )
    metrics = dict(portfolio.metrics)
    metrics["booster_signals"] = sum(
        int(result.metrics["signals"]) for result in booster_results.values()
    )
    metrics["booster_entries"] = sum(
        int(result.metrics["executed_entries"]) for result in booster_results.values()
    )
    metrics["booster_closed_cycles"] = sum(
        int(result.metrics["closed_cycles"]) for result in booster_results.values()
    )
    return metrics


def _utility(metrics: dict[str, Any]) -> float:
    """Simple return/risk utility used only to rank development-period candidates."""
    return (
        float(metrics["cagr_pct"])
        - 0.20 * abs(float(metrics["mdd_pct"]))
        + 2.0 * float(metrics["sharpe"])
    )


def _development_objective(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> float:
    deltas = []
    for split in ("train", "validation"):
        deltas.append(_utility(candidate[split]) - _utility(baseline[split]))
    weakest = min(deltas)
    average = sum(deltas) / len(deltas)
    return weakest + 0.30 * average


def _flatten_row(
    weights: dict[str, int],
    train: dict[str, Any],
    validation: dict[str, Any],
    objective: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {**weights, "objective": round(objective, 6)}
    for name, metrics in (("train", train), ("validation", validation)):
        for key in (
            "total_return_pct",
            "cagr_pct",
            "mdd_pct",
            "sharpe",
            "sortino",
            "average_exposure_pct",
            "booster_signals",
            "booster_entries",
            "booster_closed_cycles",
        ):
            row[f"{name}_{key}"] = metrics[key]
    return row


def _is_development_eligible(row: dict[str, Any], baseline_row: dict[str, Any]) -> bool:
    # Prevent the optimizer from winning simply by deleting most booster activity.
    for split in ("train", "validation"):
        if float(row[f"{split}_total_return_pct"]) <= 0:
            return False
        minimum_entries = max(1, int(0.60 * baseline_row[f"{split}_booster_entries"]))
        if int(row[f"{split}_booster_entries"]) < minimum_entries:
            return False
        baseline_mdd = abs(float(baseline_row[f"{split}_mdd_pct"]))
        candidate_mdd = abs(float(row[f"{split}_mdd_pct"]))
        if candidate_mdd > baseline_mdd + 5.0:
            return False
    return True


def _dominates_baseline(row: dict[str, Any], baseline_row: dict[str, Any]) -> bool:
    for split in ("train", "validation"):
        if float(row[f"{split}_cagr_pct"]) < float(baseline_row[f"{split}_cagr_pct"]):
            return False
        if float(row[f"{split}_mdd_pct"]) < float(baseline_row[f"{split}_mdd_pct"]):
            return False
        if float(row[f"{split}_sharpe"]) < float(baseline_row[f"{split}_sharpe"]):
            return False
    return True


def _compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metrics[key]
        for key in (
            "total_return_pct",
            "cagr_pct",
            "mdd_pct",
            "sharpe",
            "sortino",
            "average_exposure_pct",
            "booster_signals",
            "booster_entries",
            "booster_closed_cycles",
            "component_fills",
            "capital_ceiling",
            "maximum_invested_cost",
        )
        if key in metrics
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/score_weight_research.json")
    parser.add_argument("--csv", type=Path, default=ROOT / "reports/score_weight_grid.csv")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = _prepare_frames(config, end, args.refresh)
    original_engine_calculate_score = backtest_engine_module.calculate_score
    backtest_engine_module.calculate_score = _research_calculate_score
    try:
        baseline_dev = {
            split: _evaluate(
                config,
                frames,
                BASE_WEIGHTS,
                SPLITS[split][0],
                SPLITS[split][1] or end,
                args.slippage,
            )
            for split in ("train", "validation")
        }
        baseline_row = _flatten_row(
            BASE_WEIGHTS,
            baseline_dev["train"],
            baseline_dev["validation"],
            0.0,
        )
        rows: list[dict[str, Any]] = []
        candidates = _candidate_grid()
        for index, weights in enumerate(candidates, start=1):
            dev = {
                split: _evaluate(
                    config,
                    frames,
                    weights,
                    SPLITS[split][0],
                    SPLITS[split][1] or end,
                    args.slippage,
                )
                for split in ("train", "validation")
            }
            objective = _development_objective(dev, baseline_dev)
            row = _flatten_row(weights, dev["train"], dev["validation"], objective)
            row["eligible"] = _is_development_eligible(row, baseline_row)
            row["dominates_baseline"] = _dominates_baseline(row, baseline_row)
            row["distance_from_baseline"] = sum(
                abs(weights[key] - BASE_WEIGHTS[key]) for key in BASE_WEIGHTS
            )
            rows.append(row)
            if index % 25 == 0 or index == len(candidates):
                print(f"evaluated {index}/{len(candidates)}", flush=True)

        frame = pd.DataFrame(rows)
        frame = frame.sort_values(
            ["eligible", "objective", "distance_from_baseline"],
            ascending=[False, False, True],
        ).reset_index(drop=True)
        eligible = frame[frame["eligible"]]
        if eligible.empty:
            raise RuntimeError("no eligible score-weight candidate")
        locked = eligible.iloc[0].to_dict()
        locked_weights = {key: int(locked[key]) for key in BASE_WEIGHTS}

        final_periods = {}
        for split in ("oos", "recent", "full"):
            start, split_end = SPLITS[split]
            final_periods[split] = {
                "baseline": _compact(
                    _evaluate(config, frames, BASE_WEIGHTS, start, split_end or end, args.slippage)
                ),
                "candidate": _compact(
                    _evaluate(config, frames, locked_weights, start, split_end or end, args.slippage)
                ),
            }

        report = {
            "strategy_version": config.version,
            "config_version": config.config_version,
            "research_end": end,
            "slippage": args.slippage,
            "method": {
                "base_weights": BASE_WEIGHTS,
                "candidate_count": len(candidates),
                "grid_step": 5,
                "ranges": {
                    "regime": [15, 35],
                    "oversold": [25, 45],
                    "reversal": [10, 30],
                    "volume": [5, 20],
                    "atr": [0, 15],
                },
                "selection_periods": {
                    "train": SPLITS["train"],
                    "validation": SPLITS["validation"],
                },
                "holdout_period": [SPLITS["oos"][0], end],
                "note": (
                    "Production nonlinear calibration is retained. Only calibrated component "
                    "maximum weights are rescaled; total maximum stays 100 and entry score stays 55."
                ),
            },
            "development_baseline": {
                name: _compact(metrics) for name, metrics in baseline_dev.items()
            },
            "locked_candidate": {
                "weights": locked_weights,
                "objective": locked["objective"],
                "eligible": bool(locked["eligible"]),
                "dominates_baseline": bool(locked["dominates_baseline"]),
                "development": {
                    "train": {
                        key.removeprefix("train_"): value
                        for key, value in locked.items()
                        if key.startswith("train_")
                    },
                    "validation": {
                        key.removeprefix("validation_"): value
                        for key, value in locked.items()
                        if key.startswith("validation_")
                    },
                },
            },
            "final_periods": final_periods,
            "top_20_development": frame.head(20).to_dict(orient="records"),
            "robust_dominators": frame[frame["dominates_baseline"]].head(20).to_dict(orient="records"),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        frame.to_csv(args.csv, index=False)

        print("\n=== LOCKED DEVELOPMENT CANDIDATE ===")
        print(json.dumps(report["locked_candidate"], ensure_ascii=False, indent=2))
        print("\n=== FINAL HOLDOUT / RECENT / FULL ===")
        print(json.dumps(final_periods, ensure_ascii=False, indent=2))
        print("\n=== TOP 10 DEVELOPMENT ===")
        columns = [
            "regime",
            "oversold",
            "reversal",
            "volume",
            "atr",
            "objective",
            "eligible",
            "dominates_baseline",
            "train_cagr_pct",
            "train_mdd_pct",
            "train_sharpe",
            "validation_cagr_pct",
            "validation_mdd_pct",
            "validation_sharpe",
        ]
        print(frame.loc[:, columns].head(10).to_string(index=False))
        print(f"saved={args.output}")
        print(f"saved={args.csv}")
    finally:
        backtest_engine_module.calculate_score = original_engine_calculate_score
        ACTIVE_WEIGHTS.clear()
        ACTIVE_WEIGHTS.update(BASE_WEIGHTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
