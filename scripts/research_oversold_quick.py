#!/usr/bin/env python3
"""Quick directional cross-check for the V3.1.1 oversold internals study."""

from __future__ import annotations

import json
from pathlib import Path

import jd_holdings.backtest.engine as backtest_engine_module
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from scripts import research_oversold_internals as research

OUTPUT = Path("reports/oversold_quick.json")


def _multipliers(**overrides: float) -> dict[str, float]:
    values = {name: 1.0 for name in research.COMPONENTS}
    values.update(overrides)
    return values


def _variants() -> dict[str, dict[str, int] | None]:
    variants: dict[str, dict[str, int] | None] = {"baseline": None}
    for dropped in research.COMPONENTS:
        values = _multipliers()
        values[dropped] = 0.0
        variants[f"ablate_{dropped}"] = research._normalize(values)

    for boosted in research.COMPONENTS:
        values = _multipliers()
        values[boosted] = 1.5
        variants[f"boost_{boosted}"] = research._normalize(values)

    variants["fast_oversold"] = research._normalize(
        _multipliers(cci5=1.5, cci10=0.5, rsi5=1.5, rsi14=0.5)
    )
    variants["slow_oversold"] = research._normalize(
        _multipliers(cci5=0.5, cci10=1.5, rsi5=0.5, rsi14=1.5)
    )
    variants["cci_heavy"] = research._normalize(
        _multipliers(cci5=1.5, cci10=1.5, rsi5=0.5, rsi14=0.5, bollinger=0.5)
    )
    variants["rsi_heavy"] = research._normalize(
        _multipliers(cci5=0.5, cci10=0.5, rsi5=1.5, rsi14=1.5, bollinger=1.0)
    )
    variants["balanced"] = {
        "cci5": 10,
        "cci10": 10,
        "rsi5": 8,
        "rsi14": 6,
        "bollinger": 6,
    }
    return variants


def _summary(metrics: dict) -> dict:
    keys = (
        "total_return_pct",
        "cagr_pct",
        "mdd_pct",
        "sharpe",
        "sortino",
        "average_exposure_pct",
        "booster_entries",
        "booster_signals",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def main() -> int:
    config = load_config(research.ROOT / "strategy.yaml")
    end = MarketClock().latest_completed_session().isoformat()
    frames = research._prepare_frames(config, end, False)
    variants = _variants()
    original_score = backtest_engine_module.calculate_score
    backtest_engine_module.calculate_score = research._research_score
    try:
        baseline_development = {
            split: research._evaluate(
                config,
                frames,
                None,
                start,
                split_end,
                0.001,
            )
            for split, (start, split_end) in research.DEVELOPMENT.items()
        }
        rows = []
        for label, allocation in variants.items():
            development = {
                split: research._evaluate(
                    config,
                    frames,
                    allocation,
                    start,
                    split_end,
                    0.001,
                )
                for split, (start, split_end) in research.DEVELOPMENT.items()
            }
            objective = research._objective(development, baseline_development)
            rows.append(
                {
                    "label": label,
                    "allocation": (
                        research._allocation_text(research.BASE_MAXIMA)
                        if allocation is None
                        else research._allocation_text(allocation)
                    ),
                    "objective": round(objective, 6),
                    "train": _summary(development["train"]),
                    "validation": _summary(development["validation"]),
                }
            )
            print(f"development {label}", flush=True)

        ranked = sorted(rows, key=lambda row: row["objective"], reverse=True)
        final = {}
        for label, allocation in variants.items():
            final[label] = {}
            for split, (start, split_end) in research.FINAL.items():
                metrics = research._evaluate(
                    config,
                    frames,
                    allocation,
                    start,
                    split_end or end,
                    0.001,
                )
                final[label][split] = _summary(metrics)
            print(f"final {label}", flush=True)

        report = {
            "research_end": end,
            "variant_count": len(variants),
            "baseline_allocation": research.BASE_MAXIMA,
            "ranked_development": ranked,
            "final": final,
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("\n=== QUICK TOP ===", flush=True)
        for row in ranked[:8]:
            label = row["label"]
            print(
                label,
                row["allocation"],
                "obj=",
                row["objective"],
                "oos=",
                round(float(final[label]["oos"]["cagr_pct"]), 2),
                round(float(final[label]["oos"]["mdd_pct"]), 2),
                "recent=",
                round(float(final[label]["recent"]["cagr_pct"]), 2),
                round(float(final[label]["recent"]["mdd_pct"]), 2),
                flush=True,
            )
    finally:
        backtest_engine_module.calculate_score = original_score
        research.ACTIVE_ALLOCATION = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
