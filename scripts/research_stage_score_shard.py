#!/usr/bin/env python3
"""Evaluate one S1 shard of the V3.1.1 S1/S2/S3 threshold grid."""

from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path

import pandas as pd

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from scripts import research_stage_score_thresholds as base

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--s1", type=int, choices=base.STAGE_VALUES, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = base._prepare_frames(config, end, args.refresh)
    baseline = {
        split: base._evaluate(config, frames, base.BASELINE, start, split_end, args.slippage)
        for split, (start, split_end) in base.DEVELOPMENT.items()
    }

    rows = []
    grid = list(product(base.STAGE_VALUES, repeat=2))
    for index, (s2, s3) in enumerate(grid, start=1):
        thresholds = (args.s1, s2, s3)
        development = {
            split: base._evaluate(config, frames, thresholds, start, split_end, args.slippage)
            for split, (start, split_end) in base.DEVELOPMENT.items()
        }
        rows.append(base._row(thresholds, development, baseline))
        print(
            f"S1={args.s1} evaluated {index}/{len(grid)}: {args.s1}/{s2}/{s3}",
            flush=True,
        )

    frame = pd.DataFrame(rows).sort_values(
        ["eligible", "objective"],
        ascending=[False, False],
    )
    report = {
        "strategy_version": config.version,
        "research_end": end,
        "s1": args.s1,
        "candidate_count": len(grid),
        "baseline_development": {
            split: base._compact(metrics) for split, metrics in baseline.items()
        },
        "top5": frame.head(5).to_dict(orient="records"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    frame.to_csv(args.csv, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
