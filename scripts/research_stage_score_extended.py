#!/usr/bin/env python3
"""Audit actual S2/S3 signal scores and test stricter additional-entry score floors."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from scripts import research_stage_score_thresholds as base

ROOT = Path(__file__).resolve().parents[1]
S1 = 55
ADDITIONAL_VALUES = (55, 65, 70, 75, 80, 85, 90)
BASELINE = (55, 55, 55)


def _percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    series = pd.Series(values, dtype=float)
    return float(series.quantile(q))


def _baseline_signal_distribution(config, frames, end: str, slippage: float) -> dict[str, Any]:
    scenario = base._config_with_thresholds(config, *BASELINE)
    scores: dict[int, list[int]] = defaultdict(list)
    rows: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for symbol in scenario.enabled_symbols:
        sector_data = None
        if symbol == "SOXL":
            sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
        result = StrategyBacktestEngine(scenario).run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start="2011-01-01",
            end=end,
            slippage=slippage,
            indicators_precomputed=True,
            sector_data=sector_data,
        )
        for signal in result.signals:
            stage = int(signal.get("target_stage") or 0)
            if stage not in (1, 2, 3):
                continue
            score = int(signal["score"])
            scores[stage].append(score)
            rows[stage].append(
                {
                    "symbol": symbol,
                    "trade_date": signal["trade_date"],
                    "score": score,
                    "action": signal["action"],
                }
            )

    summary: dict[str, Any] = {}
    for stage in (1, 2, 3):
        values = scores[stage]
        ordered = sorted(rows[stage], key=lambda row: (row["score"], row["trade_date"], row["symbol"]))
        summary[f"stage{stage}"] = {
            "count": len(values),
            "min": min(values) if values else None,
            "q25": _percentile(values, 0.25),
            "median": float(median(values)) if values else None,
            "q75": _percentile(values, 0.75),
            "max": max(values) if values else None,
            "below_65": sum(value < 65 for value in values),
            "below_70": sum(value < 70 for value in values),
            "below_75": sum(value < 75 for value in values),
            "below_80": sum(value < 80 for value in values),
            "below_85": sum(value < 85 for value in values),
            "below_90": sum(value < 90 for value in values),
            "lowest_signals": ordered[:10],
        }
    return summary


def _compact_dev(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metrics[key]
        for key in (
            "cagr_pct",
            "mdd_pct",
            "sharpe",
            "sortino",
            "average_exposure_pct",
            "booster_entries",
            "cycles_stage2",
            "cycles_stage3",
        )
    }


def _row(
    s2: int,
    s3: int,
    development: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = base._row((S1, s2, s3), development, baseline)
    row["s2"] = s2
    row["s3"] = s3
    return row


def _add_locked(
    locked: dict[str, tuple[int, int, int]],
    label: str,
    spec: tuple[int, int, int],
) -> None:
    if spec not in locked.values():
        locked[label] = spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/stage_score_extended.json")
    parser.add_argument("--csv", type=Path, default=ROOT / "reports/stage_score_extended_grid.csv")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = base._prepare_frames(config, end, args.refresh)
    signal_distribution = _baseline_signal_distribution(config, frames, end, args.slippage)

    baseline_dev = {
        split: base._evaluate(config, frames, BASELINE, start, split_end, args.slippage)
        for split, (start, split_end) in base.DEVELOPMENT.items()
    }

    rows: list[dict[str, Any]] = []
    for s2 in ADDITIONAL_VALUES:
        for s3 in ADDITIONAL_VALUES:
            development = {
                split: base._evaluate(
                    config,
                    frames,
                    (S1, s2, s3),
                    start,
                    split_end,
                    args.slippage,
                )
                for split, (start, split_end) in base.DEVELOPMENT.items()
            }
            rows.append(_row(s2, s3, development, baseline_dev))
            print(f"evaluated S1/S2/S3={S1}/{s2}/{s3}", flush=True)

    frame = pd.DataFrame(rows).sort_values(
        ["eligible", "objective"],
        ascending=[False, False],
    )
    eligible = frame[frame["eligible"]]
    pool = eligible if not eligible.empty else frame

    locked: dict[str, tuple[int, int, int]] = {"baseline_55_55_55": BASELINE}
    for rank, (_, row) in enumerate(pool.head(5).iterrows(), start=1):
        _add_locked(locked, f"top_{rank}", (S1, int(row["s2"]), int(row["s3"])))
    for threshold in (65, 70, 75, 80, 85, 90):
        _add_locked(locked, f"flat_additional_{threshold}", (S1, threshold, threshold))
    stricter = pool[pool["s2"] <= pool["s3"]]
    if not stricter.empty:
        row = stricter.iloc[0]
        _add_locked(locked, "best_stricter_deeper", (S1, int(row["s2"]), int(row["s3"])))

    final_periods: dict[str, dict[str, dict[str, Any]]] = {}
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
            "s1_fixed": S1,
            "additional_values": list(ADDITIONAL_VALUES),
            "candidate_count": len(frame),
            "baseline": list(BASELINE),
            "selection": "Train 2011-2018 + validation 2019-2022 only; OOS opened after locking.",
            "slippage": args.slippage,
        },
        "baseline_signal_score_distribution": signal_distribution,
        "baseline_development": {
            split: _compact_dev(metrics) for split, metrics in baseline_dev.items()
        },
        "development_top10": frame.head(10).to_dict(orient="records"),
        "development_dominators": frame[frame["dominates_development"]].to_dict(orient="records"),
        "locked_specs": {label: list(spec) for label, spec in locked.items()},
        "final_periods": final_periods,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    frame.to_csv(args.csv, index=False)

    print("=== BASELINE SIGNAL SCORE DISTRIBUTION ===")
    for stage, summary in signal_distribution.items():
        print(
            stage,
            f"count={summary['count']}",
            f"min={summary['min']}",
            f"median={summary['median']}",
            f"max={summary['max']}",
        )
    print("\n=== DEVELOPMENT TOP 10 ===")
    print(
        frame[
            [
                "s1",
                "s2",
                "s3",
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
    print("\n=== OOS LOCKED ===")
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
