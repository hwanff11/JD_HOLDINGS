#!/usr/bin/env python3
"""Fast directional cross-check for V3.1.1 score component maxima."""

from __future__ import annotations

import json
from pathlib import Path

import jd_holdings.backtest.engine as backtest_engine_module

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from scripts.research_score_weights import (
    BASE_WEIGHTS,
    SPLITS,
    _compact,
    _development_objective,
    _evaluate,
    _flatten_row,
    _is_development_eligible,
    _prepare_frames,
    _research_calculate_score,
)

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    BASE_WEIGHTS,
    {"regime": 30, "oversold": 35, "reversal": 20, "volume": 10, "atr": 5},
    {"regime": 35, "oversold": 30, "reversal": 20, "volume": 10, "atr": 5},
    {"regime": 25, "oversold": 35, "reversal": 25, "volume": 10, "atr": 5},
    {"regime": 25, "oversold": 30, "reversal": 30, "volume": 10, "atr": 5},
    {"regime": 25, "oversold": 35, "reversal": 20, "volume": 15, "atr": 5},
    {"regime": 25, "oversold": 30, "reversal": 20, "volume": 20, "atr": 5},
    {"regime": 25, "oversold": 35, "reversal": 20, "volume": 10, "atr": 10},
    {"regime": 25, "oversold": 30, "reversal": 20, "volume": 10, "atr": 15},
    {"regime": 30, "oversold": 30, "reversal": 20, "volume": 15, "atr": 5},
    {"regime": 30, "oversold": 30, "reversal": 25, "volume": 10, "atr": 5},
    {"regime": 30, "oversold": 30, "reversal": 20, "volume": 10, "atr": 10},
    {"regime": 30, "oversold": 25, "reversal": 25, "volume": 15, "atr": 5},
    {"regime": 25, "oversold": 30, "reversal": 25, "volume": 15, "atr": 5},
    {"regime": 20, "oversold": 35, "reversal": 25, "volume": 15, "atr": 5},
    {"regime": 35, "oversold": 25, "reversal": 25, "volume": 10, "atr": 5},
    {"regime": 30, "oversold": 25, "reversal": 20, "volume": 15, "atr": 10},
    {"regime": 25, "oversold": 25, "reversal": 25, "volume": 15, "atr": 10},
]


def main() -> int:
    config = load_config(ROOT / "strategy.yaml")
    end = MarketClock().latest_completed_session().isoformat()
    frames = _prepare_frames(config, end, False)
    original = backtest_engine_module.calculate_score
    backtest_engine_module.calculate_score = _research_calculate_score
    try:
        baseline_dev = {
            split: _evaluate(
                config,
                frames,
                BASE_WEIGHTS,
                SPLITS[split][0],
                SPLITS[split][1] or end,
                0.001,
            )
            for split in ("train", "validation")
        }
        baseline_row = _flatten_row(
            BASE_WEIGHTS, baseline_dev["train"], baseline_dev["validation"], 0.0
        )
        rows = []
        dev_by_key = {}
        for weights in CANDIDATES:
            dev = {
                split: _evaluate(
                    config,
                    frames,
                    weights,
                    SPLITS[split][0],
                    SPLITS[split][1] or end,
                    0.001,
                )
                for split in ("train", "validation")
            }
            objective = _development_objective(dev, baseline_dev)
            row = _flatten_row(weights, dev["train"], dev["validation"], objective)
            row["eligible"] = _is_development_eligible(row, baseline_row)
            rows.append(row)
            dev_by_key[tuple(weights.values())] = dev
        rows.sort(key=lambda row: (bool(row["eligible"]), float(row["objective"])), reverse=True)
        winner = rows[0]
        weights = {key: int(winner[key]) for key in BASE_WEIGHTS}
        final = {}
        for split in ("oos", "recent", "full"):
            start, split_end = SPLITS[split]
            final[split] = {
                "baseline": _compact(
                    _evaluate(config, frames, BASE_WEIGHTS, start, split_end or end, 0.001)
                ),
                "candidate": _compact(
                    _evaluate(config, frames, weights, start, split_end or end, 0.001)
                ),
            }
        report = {
            "research_end": end,
            "candidate_count": len(CANDIDATES),
            "winner": weights,
            "winner_objective": winner["objective"],
            "development": dev_by_key[tuple(weights.values())],
            "final": final,
            "ranking": rows,
        }
        output = ROOT / "reports/score_weight_fast.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        backtest_engine_module.calculate_score = original


if __name__ == "__main__":
    raise SystemExit(main())
