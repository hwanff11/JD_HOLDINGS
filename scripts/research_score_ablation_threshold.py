#!/usr/bin/env python3
"""Research JDSS V3.1.1 score-component value and score-floor sensitivity.

Production scoring calibration and all trading logic stay unchanged. The study removes
one component's contribution from the total score at a time while preserving hard
safety gates, and sweeps a common S1/S2/S3 score floor across 45..70. Candidate
selection uses only 2011-2018 train and 2019-2022 validation periods. The 2023+
holdout is opened only after development candidates are locked.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

import jd_holdings.backtest.engine as backtest_engine_module
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.indicators import calculate_indicators
from jd_holdings.core.models import ScoreResult
from jd_holdings.core.scoring import (
    calculate_grade,
    calculate_score as production_calculate_score,
)
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ("regime", "oversold", "reversal", "volume", "atr")
THRESHOLDS = (45, 50, 55, 60, 65, 70)
SPLITS = {
    "train": ("2011-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2022-12-30"),
    "oos": ("2023-01-01", None),
    "recent": ("2022-01-01", None),
    "full": ("2011-01-01", None),
}
ACTIVE_ABLATION: str | None = None


def _research_calculate_score(snapshot, regime, config) -> ScoreResult:
    """Remove only one component from total score while retaining gate fields."""
    base = production_calculate_score(snapshot, regime, config)
    removed = 0
    if ACTIVE_ABLATION is not None:
        removed = int(getattr(base, f"{ACTIVE_ABLATION}_score"))
    total = max(0, int(base.total) - removed)
    return ScoreResult(
        total=total,
        grade=calculate_grade(total, config),
        regime=base.regime,
        regime_score=base.regime_score,
        oversold_score=base.oversold_score,
        reversal_score=base.reversal_score,
        volume_score=base.volume_score,
        atr_score=base.atr_score,
        raw_regime_score=base.raw_regime_score,
        raw_oversold_score=base.raw_oversold_score,
        raw_reversal_score=base.raw_reversal_score,
        raw_volume_score=base.raw_volume_score,
        raw_atr_score=base.raw_atr_score,
    )


def _config_with_threshold(config: StrategyConfig, threshold: int) -> StrategyConfig:
    global_config = replace(config.global_, entry_score=threshold)
    stages = {
        stage: replace(rule, min_score=threshold)
        for stage, rule in config.additional_entry.stages.items()
    }
    additional_entry = replace(config.additional_entry, stages=stages)
    return replace(
        config,
        global_=global_config,
        additional_entry=additional_entry,
    )


def _prepare_frames(config: StrategyConfig, end: str, refresh: bool):
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = "2009-11-27"
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    raw = {
        symbol: source.daily(symbol, warmup_start, end, refresh=refresh)
        for symbol in symbols
    }
    return {
        symbol: calculate_indicators(frame, config)
        for symbol, frame in raw.items()
    }


def _evaluate(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    ablation: str | None,
    threshold: int,
    start: str,
    end: str,
    slippage: float,
) -> dict[str, Any]:
    global ACTIVE_ABLATION
    ACTIVE_ABLATION = ablation
    scenario_config = _config_with_threshold(config, threshold)
    booster_results = {}
    for symbol in scenario_config.enabled_symbols:
        sector_data = None
        if symbol == "SOXL":
            sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
        booster_results[symbol] = StrategyBacktestEngine(scenario_config).run(
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
    portfolio = PortfolioBacktestEngine(scenario_config).run(
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
        int(result.metrics["executed_entries"])
        for result in booster_results.values()
    )
    metrics["booster_closed_cycles"] = sum(
        int(result.metrics["closed_cycles"])
        for result in booster_results.values()
    )
    return metrics


def _utility(metrics: dict[str, Any]) -> float:
    return (
        float(metrics["cagr_pct"])
        - 0.20 * abs(float(metrics["mdd_pct"]))
        + 2.0 * float(metrics["sharpe"])
    )


def _development_objective(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> float:
    deltas = [
        _utility(candidate[split]) - _utility(baseline[split])
        for split in ("train", "validation")
    ]
    return min(deltas) + 0.30 * (sum(deltas) / len(deltas))


def _flatten_row(
    ablation: str | None,
    threshold: int,
    train: dict[str, Any],
    validation: dict[str, Any],
    objective: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ablation": ablation or "none",
        "threshold": threshold,
        "objective": round(objective, 6),
    }
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
    )
    for name, metrics in (("train", train), ("validation", validation)):
        for key in keys:
            row[f"{name}_{key}"] = metrics[key]
    return row


def _eligible(row: dict[str, Any], baseline_row: dict[str, Any]) -> bool:
    for split in ("train", "validation"):
        if float(row[f"{split}_total_return_pct"]) <= 0:
            return False
        baseline_entries = int(baseline_row[f"{split}_booster_entries"])
        minimum_entries = max(1, int(0.50 * baseline_entries))
        if int(row[f"{split}_booster_entries"]) < minimum_entries:
            return False
        baseline_mdd = abs(float(baseline_row[f"{split}_mdd_pct"]))
        candidate_mdd = abs(float(row[f"{split}_mdd_pct"]))
        if candidate_mdd > baseline_mdd + 5.0:
            return False
    return True


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
        "component_fills",
        "maximum_invested_cost",
        "annual_returns_pct",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def _row_spec(row: dict[str, Any]) -> tuple[str | None, int]:
    ablation = str(row["ablation"])
    return (None if ablation == "none" else ablation, int(row["threshold"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/score_ablation_threshold.json",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "reports/score_ablation_threshold_grid.csv",
    )
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = _prepare_frames(config, end, args.refresh)
    original = backtest_engine_module.calculate_score
    backtest_engine_module.calculate_score = _research_calculate_score
    try:
        baseline_dev = {
            split: _evaluate(
                config,
                frames,
                None,
                55,
                SPLITS[split][0],
                SPLITS[split][1] or end,
                args.slippage,
            )
            for split in ("train", "validation")
        }
        baseline_row = _flatten_row(
            None,
            55,
            baseline_dev["train"],
            baseline_dev["validation"],
            0.0,
        )

        rows: list[dict[str, Any]] = []
        variants: tuple[str | None, ...] = (None, *COMPONENTS)
        total_runs = len(variants) * len(THRESHOLDS)
        run_index = 0
        for ablation in variants:
            for threshold in THRESHOLDS:
                run_index += 1
                dev = {
                    split: _evaluate(
                        config,
                        frames,
                        ablation,
                        threshold,
                        SPLITS[split][0],
                        SPLITS[split][1] or end,
                        args.slippage,
                    )
                    for split in ("train", "validation")
                }
                objective = _development_objective(dev, baseline_dev)
                row = _flatten_row(
                    ablation,
                    threshold,
                    dev["train"],
                    dev["validation"],
                    objective,
                )
                row["eligible"] = _eligible(row, baseline_row)
                rows.append(row)
                print(
                    f"evaluated {run_index}/{total_runs}: "
                    f"ablation={ablation or 'none'} threshold={threshold}",
                    flush=True,
                )

        frame = pd.DataFrame(rows)
        frame = frame.sort_values(
            ["eligible", "objective"],
            ascending=[False, False],
        ).reset_index(drop=True)

        threshold_frame = frame[frame["ablation"] == "none"]
        threshold_winner = threshold_frame.iloc[0].to_dict()
        eligible = frame[frame["eligible"]]
        overall_winner = eligible.iloc[0].to_dict()

        variant_winners: dict[str, dict[str, Any]] = {}
        for variant in ("none", *COMPONENTS):
            subset = frame[frame["ablation"] == variant]
            eligible_subset = subset[subset["eligible"]]
            chosen = eligible_subset.iloc[0] if not eligible_subset.empty else subset.iloc[0]
            variant_winners[variant] = chosen.to_dict()

        locked_specs: dict[str, tuple[str | None, int]] = {
            "baseline_55": (None, 55),
            "threshold_winner": _row_spec(threshold_winner),
            "overall_winner": _row_spec(overall_winner),
        }
        for component in COMPONENTS:
            locked_specs[f"ablate_{component}"] = _row_spec(
                variant_winners[component]
            )

        final_periods: dict[str, dict[str, dict[str, Any]]] = {}
        for split in ("oos", "recent", "full"):
            start, split_end = SPLITS[split]
            final_periods[split] = {}
            for label, (ablation, threshold) in locked_specs.items():
                final_periods[split][label] = _compact(
                    _evaluate(
                        config,
                        frames,
                        ablation,
                        threshold,
                        start,
                        split_end or end,
                        args.slippage,
                    )
                )

        report = {
            "strategy_version": config.version,
            "config_version": config.config_version,
            "research_end": end,
            "slippage": args.slippage,
            "method": {
                "thresholds": list(THRESHOLDS),
                "components": list(COMPONENTS),
                "development_periods": {
                    "train": SPLITS["train"],
                    "validation": SPLITS["validation"],
                },
                "holdout_period": [SPLITS["oos"][0], end],
                "score_floor_rule": (
                    "The same threshold is applied to S1 entry_score and S2/S3 min_score."
                ),
                "ablation_rule": (
                    "Only the selected component contribution is removed from total score. "
                    "Component fields remain available so RED blocking and minimum reversal "
                    "hard gates stay unchanged."
                ),
            },
            "development_baseline_55": {
                split: _compact(metrics)
                for split, metrics in baseline_dev.items()
            },
            "threshold_only": threshold_frame.to_dict(orient="records"),
            "variant_winners": variant_winners,
            "overall_winner": overall_winner,
            "locked_specs": {
                label: {
                    "ablation": ablation or "none",
                    "threshold": threshold,
                }
                for label, (ablation, threshold) in locked_specs.items()
            },
            "final_periods": final_periods,
            "top_15_development": frame.head(15).to_dict(orient="records"),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        frame.to_csv(args.csv, index=False)

        print("\n=== THRESHOLD ONLY ===")
        cols = [
            "threshold",
            "objective",
            "eligible",
            "train_cagr_pct",
            "train_mdd_pct",
            "validation_cagr_pct",
            "validation_mdd_pct",
        ]
        print(threshold_frame[cols].to_string(index=False))
        print("\n=== VARIANT WINNERS ===")
        for variant, row in variant_winners.items():
            print(
                variant,
                "threshold=",
                int(row["threshold"]),
                "objective=",
                round(float(row["objective"]), 4),
                "eligible=",
                bool(row["eligible"]),
            )
        print("\n=== FINAL OOS ===")
        for label, metrics in final_periods["oos"].items():
            print(
                label,
                f"CAGR={metrics['cagr_pct']:+.2f}%",
                f"MDD={metrics['mdd_pct']:.2f}%",
                f"Sharpe={metrics['sharpe']:.3f}",
                f"entries={metrics['booster_entries']}",
            )
        print(f"saved={args.output}")
        print(f"saved={args.csv}")
    finally:
        backtest_engine_module.calculate_score = original
        globals()["ACTIVE_ABLATION"] = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
