#!/usr/bin/env python3
"""Frequency-match component ablations against the V3.1.1 score-55 baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from scripts import research_score_ablation_threshold as base
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock

ROOT = Path(__file__).resolve().parents[1]
RANGES = {
    "regime": tuple(range(25, 56, 5)),
    "oversold": tuple(range(20, 56, 5)),
    "reversal": tuple(range(35, 56, 5)),
    "volume": tuple(range(45, 61, 5)),
    "atr": tuple(range(45, 56, 5)),
}


def _frequency_distance(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> float:
    distance = 0.0
    for split in ("train", "validation"):
        base_entries = max(1, int(baseline[split]["booster_entries"]))
        cand_entries = int(candidate[split]["booster_entries"])
        distance += abs(cand_entries - base_entries) / base_entries

        base_exposure = max(0.01, float(baseline[split]["average_exposure_pct"]))
        cand_exposure = float(candidate[split]["average_exposure_pct"])
        distance += 0.25 * abs(cand_exposure - base_exposure) / base_exposure
    return distance


def _row(
    component: str,
    threshold: int,
    train: dict[str, Any],
    validation: dict[str, Any],
    distance: float,
    objective: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "component": component,
        "threshold": threshold,
        "frequency_distance": round(distance, 6),
        "objective": round(objective, 6),
    }
    for split, metrics in (("train", train), ("validation", validation)):
        for key in (
            "cagr_pct",
            "mdd_pct",
            "sharpe",
            "sortino",
            "average_exposure_pct",
            "booster_entries",
        ):
            row[f"{split}_{key}"] = metrics[key]
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/score_ablation_frequency.json",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "reports/score_ablation_frequency_grid.csv",
    )
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = base._prepare_frames(config, end, args.refresh)
    original = base.backtest_engine_module.calculate_score
    base.backtest_engine_module.calculate_score = base._research_calculate_score
    try:
        baseline_dev = {
            split: base._evaluate(
                config,
                frames,
                None,
                55,
                base.SPLITS[split][0],
                base.SPLITS[split][1] or end,
                args.slippage,
            )
            for split in ("train", "validation")
        }
        rows: list[dict[str, Any]] = []
        for component, thresholds in RANGES.items():
            for threshold in thresholds:
                dev = {
                    split: base._evaluate(
                        config,
                        frames,
                        component,
                        threshold,
                        base.SPLITS[split][0],
                        base.SPLITS[split][1] or end,
                        args.slippage,
                    )
                    for split in ("train", "validation")
                }
                distance = _frequency_distance(dev, baseline_dev)
                objective = base._development_objective(dev, baseline_dev)
                rows.append(
                    _row(
                        component,
                        threshold,
                        dev["train"],
                        dev["validation"],
                        distance,
                        objective,
                    )
                )
                print(
                    f"component={component} threshold={threshold} "
                    f"distance={distance:.4f}",
                    flush=True,
                )

        frame = pd.DataFrame(rows)
        winners: dict[str, dict[str, Any]] = {}
        for component in base.COMPONENTS:
            subset = frame[frame["component"] == component].sort_values(
                ["frequency_distance", "objective"],
                ascending=[True, False],
            )
            winners[component] = subset.iloc[0].to_dict()

        final_periods: dict[str, dict[str, dict[str, Any]]] = {}
        for split in ("oos", "recent", "full"):
            start, split_end = base.SPLITS[split]
            final_periods[split] = {
                "baseline_55": base._compact(
                    base._evaluate(
                        config,
                        frames,
                        None,
                        55,
                        start,
                        split_end or end,
                        args.slippage,
                    )
                )
            }
            for component, winner in winners.items():
                threshold = int(winner["threshold"])
                final_periods[split][f"ablate_{component}"] = base._compact(
                    base._evaluate(
                        config,
                        frames,
                        component,
                        threshold,
                        start,
                        split_end or end,
                        args.slippage,
                    )
                )

        report = {
            "strategy_version": config.version,
            "research_end": end,
            "method": {
                "baseline_threshold": 55,
                "ranges": {key: list(values) for key, values in RANGES.items()},
                "selection": (
                    "Minimize train+validation booster-entry and exposure distance "
                    "from the intact score-55 baseline; objective breaks ties."
                ),
            },
            "baseline_development": {
                split: base._compact(metrics)
                for split, metrics in baseline_dev.items()
            },
            "winners": winners,
            "final_periods": final_periods,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        frame.to_csv(args.csv, index=False)

        print("\n=== FREQUENCY MATCH WINNERS ===")
        for component, winner in winners.items():
            print(
                component,
                f"threshold={int(winner['threshold'])}",
                f"distance={float(winner['frequency_distance']):.4f}",
                f"objective={float(winner['objective']):+.4f}",
            )
        print("\n=== OOS ===")
        for label, metrics in final_periods["oos"].items():
            print(
                label,
                f"CAGR={metrics['cagr_pct']:+.2f}%",
                f"MDD={metrics['mdd_pct']:.2f}%",
                f"Sharpe={metrics['sharpe']:.3f}",
                f"entries={metrics['booster_entries']}",
            )
    finally:
        base.backtest_engine_module.calculate_score = original
        base.ACTIVE_ABLATION = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
