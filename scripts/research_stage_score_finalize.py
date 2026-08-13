#!/usr/bin/env python3
"""Combine development shards and run locked OOS/recent/full stage-threshold tests."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import pandas as pd

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from scripts import research_stage_score_thresholds as base

ROOT = Path(__file__).resolve().parents[1]


def _add_locked(
    locked: dict[str, tuple[int, int, int]],
    label: str,
    spec: tuple[int, int, int],
) -> None:
    if spec not in locked.values():
        locked[label] = spec


def _spec(row: pd.Series) -> tuple[int, int, int]:
    return int(row["s1"]), int(row["s2"]), int(row["s3"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-glob", required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.grid_glob))
    if len(paths) != len(base.STAGE_VALUES):
        raise RuntimeError(f"Expected {len(base.STAGE_VALUES)} shard CSVs, found {len(paths)}: {paths}")
    frame = pd.concat((pd.read_csv(path) for path in paths), ignore_index=True)
    if len(frame) != len(base.STAGE_VALUES) ** 3:
        raise RuntimeError(f"Expected 125 candidates, found {len(frame)}")
    frame = frame.sort_values(["eligible", "objective"], ascending=[False, False])
    eligible = frame[frame["eligible"].astype(bool)]
    candidate_pool = eligible if not eligible.empty else frame

    locked: dict[str, tuple[int, int, int]] = {"baseline_55_55_55": base.BASELINE}
    for rank, (_, row) in enumerate(candidate_pool.head(5).iterrows(), start=1):
        _add_locked(locked, f"top_{rank}", _spec(row))
    for family in ("stricter_deeper", "looser_deeper", "flat", "mixed"):
        subset = candidate_pool[candidate_pool["family"] == family]
        if not subset.empty:
            _add_locked(locked, f"best_{family}", _spec(subset.iloc[0]))
    s1_55 = candidate_pool[candidate_pool["s1"] == 55]
    if not s1_55.empty:
        _add_locked(locked, "best_s1_55", _spec(s1_55.iloc[0]))

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = base._prepare_frames(config, end, args.refresh)
    baseline_development = {
        split: base._evaluate(config, frames, base.BASELINE, start, split_end, args.slippage)
        for split, (start, split_end) in base.DEVELOPMENT.items()
    }
    final_periods: dict[str, dict[str, dict]] = {}
    for split, (start, split_end) in base.FINAL.items():
        resolved_end = split_end or end
        final_periods[split] = {
            label: base._compact(
                base._evaluate(config, frames, spec, start, resolved_end, args.slippage)
            )
            for label, spec in locked.items()
        }

    report = {
        "strategy_version": config.version,
        "research_end": end,
        "method": {
            "stage_values": list(base.STAGE_VALUES),
            "candidate_count": len(frame),
            "baseline": list(base.BASELINE),
            "selection": "Five parallel S1 shards; train 2011-2018 + validation 2019-2022 only; OOS opened after locking.",
            "fees": float(config.global_.buy_fee),
            "slippage": args.slippage,
        },
        "baseline_development": {
            split: base._compact(metrics) for split, metrics in baseline_development.items()
        },
        "development_top10": frame.head(10).to_dict(orient="records"),
        "development_dominators": frame[frame["dominates_development"].astype(bool)].to_dict(
            orient="records"
        ),
        "locked_specs": {label: list(spec) for label, spec in locked.items()},
        "final_periods": final_periods,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    frame.to_csv(args.csv, index=False)

    print("=== DEVELOPMENT TOP 10 ===")
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
    print("\n=== LOCKED OOS ===")
    for label, spec in locked.items():
        metrics = final_periods["oos"][label]
        print(
            label,
            "/".join(map(str, spec)),
            f"CAGR={metrics['cagr_pct']:+.2f}%",
            f"MDD={metrics['mdd_pct']:.2f}%",
            f"Sharpe={metrics['sharpe']:.3f}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
