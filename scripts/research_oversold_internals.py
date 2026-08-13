#!/usr/bin/env python3
"""Research the internal composition of the V3.1.1 oversold score.

The production strategy stays unchanged. Oversold remains capped at 40 and the
S1/S2/S3 score floor remains 55. Candidate selection uses 2011-2018 train and
2019-2022 validation only; 2023+ is opened after candidates are locked.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd

import jd_holdings.backtest.engine as backtest_engine_module
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.indicators import calculate_indicators, snapshot_from_row
from jd_holdings.core.models import IndicatorSnapshot, ScoreResult
from jd_holdings.core.scoring import calculate_grade
from jd_holdings.core.scoring import calculate_score as production_calculate_score
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ("cci5", "cci10", "rsi5", "rsi14", "bollinger")
BASE_MAXIMA = {"cci5": 13, "cci10": 13, "rsi5": 7, "rsi14": 4, "bollinger": 3}
OVERSOLD_MAX = 40
SCORE_FLOOR = 55
DEVELOPMENT = {
    "train": ("2011-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2022-12-30"),
}
FINAL = {
    "oos": ("2023-01-01", None),
    "recent": ("2022-01-01", None),
    "full": ("2011-01-01", None),
}
ACTIVE_ALLOCATION: dict[str, int] | None = None


@dataclass(frozen=True)
class Variant:
    label: str
    allocation: dict[str, int]
    family: str


def _lte_band_score(value: float, bands: list[list[float | int]]) -> int:
    for threshold, score in bands:
        if value <= float(threshold):
            return int(score)
    return 0


def _raw_parts(snapshot: IndicatorSnapshot, config: StrategyConfig) -> dict[str, int]:
    parts = {
        "cci5": _lte_band_score(snapshot.cci5, config.scoring["cci5"]["bands"]),
        "cci10": _lte_band_score(snapshot.cci10, config.scoring["cci10"]["bands"]),
        "rsi5": _lte_band_score(snapshot.rsi5, config.scoring["rsi5"]["bands"]),
        "rsi14": _lte_band_score(snapshot.rsi14, config.scoring["rsi14"]["bands"]),
        "bollinger": 0,
    }
    bollinger = config.scoring["bollinger"]
    close = float(snapshot.close)
    if close <= snapshot.bb_lower * float(bollinger["deep_multiplier"]):
        parts["bollinger"] = int(bollinger["deep_score"])
    elif close <= snapshot.bb_lower:
        parts["bollinger"] = int(bollinger["touch_score"])
    return parts


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def _scale_part(score: int, component: str, target_maximum: int) -> int:
    if score <= 0 or target_maximum <= 0:
        return 0
    scaled = target_maximum * score / BASE_MAXIMA[component]
    return min(target_maximum, _round_half_up(scaled))


def _calibrate_oversold(raw_score: int, config: StrategyConfig) -> int:
    calibration = config.scoring.get("calibration", {})
    if not calibration.get("enabled", False):
        return raw_score
    if raw_score <= 0:
        return 0
    if raw_score >= OVERSOLD_MAX:
        return OVERSOLD_MAX
    exponent = float(calibration.get("exponents", {}).get("oversold", 1.0))
    value = OVERSOLD_MAX * (raw_score / OVERSOLD_MAX) ** exponent
    return min(OVERSOLD_MAX, _round_half_up(value))


def _research_score(snapshot, regime, config) -> ScoreResult:
    base = production_calculate_score(snapshot, regime, config)
    if ACTIVE_ALLOCATION is None:
        return base

    parts = _raw_parts(snapshot, config)
    raw_oversold = min(
        OVERSOLD_MAX,
        sum(
            _scale_part(parts[name], name, ACTIVE_ALLOCATION[name])
            for name in COMPONENTS
        ),
    )
    oversold_score = _calibrate_oversold(raw_oversold, config)
    total = max(0, min(100, base.total - base.oversold_score + oversold_score))
    return ScoreResult(
        total=total,
        grade=calculate_grade(total, config),
        regime=base.regime,
        regime_score=base.regime_score,
        oversold_score=oversold_score,
        reversal_score=base.reversal_score,
        volume_score=base.volume_score,
        atr_score=base.atr_score,
        raw_regime_score=base.raw_regime_score,
        raw_oversold_score=raw_oversold,
        raw_reversal_score=base.raw_reversal_score,
        raw_volume_score=base.raw_volume_score,
        raw_atr_score=base.raw_atr_score,
    )


def _normalize(multipliers: dict[str, float]) -> dict[str, int]:
    weighted = {
        name: BASE_MAXIMA[name] * multipliers[name]
        for name in COMPONENTS
    }
    total = sum(weighted.values())
    if total <= 0:
        raise ValueError("At least one oversold component must remain active")

    exact = {name: OVERSOLD_MAX * weighted[name] / total for name in COMPONENTS}
    allocation = {name: math.floor(exact[name]) for name in COMPONENTS}
    remainder = OVERSOLD_MAX - sum(allocation.values())
    ranked = sorted(
        COMPONENTS,
        key=lambda name: (exact[name] - allocation[name], -COMPONENTS.index(name)),
        reverse=True,
    )
    for name in ranked[:remainder]:
        allocation[name] += 1
    return allocation


def _allocation_key(allocation: dict[str, int]) -> tuple[int, ...]:
    return tuple(allocation[name] for name in COMPONENTS)


def _allocation_text(allocation: dict[str, int]) -> str:
    return "/".join(str(allocation[name]) for name in COMPONENTS)


def _build_variants() -> tuple[list[Variant], dict[str, Variant]]:
    seen = {_allocation_key(BASE_MAXIMA)}
    variants: list[Variant] = []
    ablations: dict[str, Variant] = {}

    for dropped in COMPONENTS:
        multipliers = {name: 1.0 for name in COMPONENTS}
        multipliers[dropped] = 0.0
        allocation = _normalize(multipliers)
        variant = Variant(f"ablate_{dropped}", allocation, "ablation")
        ablations[dropped] = variant
        key = _allocation_key(allocation)
        if key not in seen:
            seen.add(key)
            variants.append(variant)

    for values in product((0.5, 1.0, 1.5), repeat=len(COMPONENTS)):
        multipliers = dict(zip(COMPONENTS, values, strict=True))
        allocation = _normalize(multipliers)
        key = _allocation_key(allocation)
        if key in seen:
            continue
        seen.add(key)
        label = "grid_" + "_".join(str(value).replace(".", "p") for value in values)
        variants.append(Variant(label, allocation, "grid"))
    return variants, ablations


def _prepare_frames(
    config: StrategyConfig,
    end: str,
    refresh: bool,
) -> dict[str, pd.DataFrame]:
    source = YFinanceDataSource(ROOT / "data" / "cache")
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    raw = {
        symbol: source.daily(symbol, "2009-11-27", end, refresh=refresh)
        for symbol in symbols
    }
    return {
        symbol: calculate_indicators(frame, config)
        for symbol, frame in raw.items()
    }


def _run_boosters(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    allocation: dict[str, int] | None,
    start: str,
    end: str,
    slippage: float,
):
    global ACTIVE_ALLOCATION
    ACTIVE_ALLOCATION = allocation
    results = {}
    for symbol in config.enabled_symbols:
        sector_data = None
        if symbol == "SOXL":
            sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
        results[symbol] = StrategyBacktestEngine(config).run(
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
    return results


def _evaluate(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    allocation: dict[str, int] | None,
    start: str,
    end: str,
    slippage: float,
) -> dict[str, Any]:
    boosters = _run_boosters(config, frames, allocation, start, end, slippage)
    portfolio = PortfolioBacktestEngine(config).run(
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
    metrics["booster_signals"] = sum(
        int(result.metrics["signals"]) for result in boosters.values()
    )
    metrics["booster_entries"] = sum(
        int(result.metrics["executed_entries"]) for result in boosters.values()
    )
    metrics["booster_closed_cycles"] = sum(
        int(result.metrics["closed_cycles"]) for result in boosters.values()
    )
    metrics["symbol_metrics"] = {
        symbol: {
            "signals": int(result.metrics["signals"]),
            "executed_entries": int(result.metrics["executed_entries"]),
            "closed_cycles": int(result.metrics["closed_cycles"]),
            "total_return_pct": float(result.metrics["total_return_pct"]),
            "cagr_pct": float(result.metrics["cagr_pct"]),
            "mdd_pct": float(result.metrics["mdd_pct"]),
            "sharpe": float(result.metrics["sharpe"]),
        }
        for symbol, result in boosters.items()
    }
    return metrics


def _utility(metrics: dict[str, Any]) -> float:
    return (
        float(metrics["cagr_pct"])
        - 0.25 * abs(float(metrics["mdd_pct"]))
        + 2.0 * float(metrics["sharpe"])
    )


def _objective(
    candidate: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> float:
    deltas = [
        _utility(candidate[split]) - _utility(baseline[split])
        for split in DEVELOPMENT
    ]
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


def _grid_row(
    variant: Variant,
    development: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "label": variant.label,
        "family": variant.family,
        "allocation": _allocation_text(variant.allocation),
        "objective": round(_objective(development, baseline), 6),
        "eligible": _eligible(development, baseline),
        "dominates_development": _dominates(development, baseline),
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
    for split, metrics in development.items():
        for key in keys:
            row[f"{split}_{key}"] = metrics[key]
    return row


def _compact(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "total_return_pct",
        "cagr_pct",
        "mdd_pct",
        "sharpe",
        "sortino",
        "average_exposure_pct",
        "component_fills",
        "maximum_invested_cost",
        "annual_returns_pct",
        "booster_signals",
        "booster_entries",
        "booster_closed_cycles",
        "symbol_metrics",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def _valid_index(frames: dict[str, pd.DataFrame], symbol: str) -> pd.DatetimeIndex:
    common = frames[symbol].index.intersection(frames["SPY"].index).intersection(frames["QQQ"].index)
    required = ["cci5", "cci10", "rsi5", "rsi14", "bb_lower"]
    valid = frames[symbol].loc[common, required].notna().all(axis=1)
    return common[valid]


def _distribution(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    baseline_boosters,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for symbol in config.enabled_symbols:
        signal_dates = {
            str(signal["trade_date"])
            for signal in baseline_boosters[symbol].signals
        }
        all_rows = []
        signal_rows = []
        for timestamp in _valid_index(frames, symbol):
            snapshot = snapshot_from_row(symbol, timestamp, frames[symbol].loc[timestamp])
            parts = _raw_parts(snapshot, config)
            all_rows.append(parts)
            if timestamp.date().isoformat() in signal_dates:
                signal_rows.append(parts)

        all_frame = pd.DataFrame(all_rows, columns=COMPONENTS)
        signal_frame = pd.DataFrame(signal_rows, columns=COMPONENTS)
        stats = {}
        for name in COMPONENTS:
            all_values = all_frame[name]
            signal_values = signal_frame[name]
            stats[name] = {
                "maximum": BASE_MAXIMA[name],
                "all_mean": round(float(all_values.mean()), 4),
                "all_nonzero_pct": round(float((all_values > 0).mean() * 100), 2),
                "signal_mean": round(float(signal_values.mean()), 4),
                "signal_nonzero_pct": round(float((signal_values > 0).mean() * 100), 2),
                "signal_distribution": {
                    str(key): int(value)
                    for key, value in Counter(signal_values.tolist()).items()
                },
            }
        report[symbol] = {
            "valid_days": len(all_frame),
            "signal_days": len(signal_frame),
            "stats": stats,
            "signal_correlation": signal_frame.corr().round(4).fillna(0.0).to_dict(),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/oversold_internals.json",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "reports/oversold_internals_grid.csv",
    )
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    if config.global_.entry_score != SCORE_FLOOR:
        raise ValueError(f"Expected production entry score {SCORE_FLOOR}")

    end = MarketClock().latest_completed_session().isoformat()
    frames = _prepare_frames(config, end, args.refresh)
    variants, ablations = _build_variants()
    print(f"variants={len(variants)} end={end}", flush=True)

    original_score = backtest_engine_module.calculate_score
    backtest_engine_module.calculate_score = _research_score
    try:
        baseline_development = {
            split: _evaluate(config, frames, None, start, split_end, args.slippage)
            for split, (start, split_end) in DEVELOPMENT.items()
        }
        baseline_variant = Variant("baseline", dict(BASE_MAXIMA), "baseline")
        rows = [
            _grid_row(
                baseline_variant,
                baseline_development,
                baseline_development,
            )
        ]
        variants_by_label = {variant.label: variant for variant in variants}

        for index, variant in enumerate(variants, start=1):
            development = {
                split: _evaluate(
                    config,
                    frames,
                    variant.allocation,
                    start,
                    split_end,
                    args.slippage,
                )
                for split, (start, split_end) in DEVELOPMENT.items()
            }
            rows.append(_grid_row(variant, development, baseline_development))
            if index % 20 == 0 or index == len(variants):
                print(f"evaluated {index}/{len(variants)}", flush=True)

        grid = pd.DataFrame(rows).sort_values(
            ["eligible", "dominates_development", "objective"],
            ascending=[False, False, False],
        )
        grid = grid.reset_index(drop=True)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        grid.to_csv(args.csv, index=False)

        eligible = grid[(grid["eligible"]) & (grid["label"] != "baseline")]
        locked_labels = eligible.head(8)["label"].tolist()
        for variant in ablations.values():
            if variant.label not in locked_labels:
                locked_labels.append(variant.label)
        locked_labels = list(dict.fromkeys(locked_labels))

        locked_variants = {"baseline": baseline_variant}
        for label in locked_labels:
            if label in variants_by_label:
                locked_variants[label] = variants_by_label[label]

        final: dict[str, dict[str, Any]] = {}
        baseline_full_boosters = None
        for split, (start, split_end) in FINAL.items():
            split_end = split_end or end
            final[split] = {}
            for label, variant in locked_variants.items():
                allocation = None if label == "baseline" else variant.allocation
                metrics = _evaluate(
                    config,
                    frames,
                    allocation,
                    start,
                    split_end,
                    args.slippage,
                )
                final[split][label] = {
                    "allocation": _allocation_text(variant.allocation),
                    "family": variant.family,
                    "metrics": _compact(metrics),
                }
            if split == "full":
                baseline_full_boosters = _run_boosters(
                    config,
                    frames,
                    None,
                    start,
                    split_end,
                    args.slippage,
                )

        assert baseline_full_boosters is not None
        top_development = grid.head(20).to_dict(orient="records")
        dominators = grid[
            (grid["dominates_development"]) & (grid["label"] != "baseline")
        ].to_dict(orient="records")
        report = {
            "strategy_version": config.version,
            "config_version": config.config_version,
            "research_end": end,
            "slippage": args.slippage,
            "baseline_allocation": BASE_MAXIMA,
            "score_floor": SCORE_FLOOR,
            "variant_count": len(variants),
            "method": {
                "components": list(COMPONENTS),
                "weight_grid_multipliers": [0.5, 1.0, 1.5],
                "development_splits": DEVELOPMENT,
                "holdout_split": [FINAL["oos"][0], end],
                "allocation_rule": (
                    "Candidate maxima are normalized back to 40 and raw band "
                    "scores are scaled proportionally to each candidate maximum."
                ),
                "ablation_rule": (
                    "One subcomponent is set to zero and its maximum is "
                    "redistributed proportionally across the remaining components."
                ),
            },
            "baseline_development": rows[0],
            "top_development": top_development,
            "development_dominators": dominators,
            "locked_labels": locked_labels,
            "final": final,
            "subcomponent_distribution": _distribution(
                config,
                frames,
                baseline_full_boosters,
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        print("\n=== DEVELOPMENT TOP 10 ===", flush=True)
        for row in top_development[:10]:
            print(
                row["label"],
                row["allocation"],
                "obj=",
                row["objective"],
                "train=",
                round(float(row["train_cagr_pct"]), 2),
                round(float(row["train_mdd_pct"]), 2),
                "validation=",
                round(float(row["validation_cagr_pct"]), 2),
                round(float(row["validation_mdd_pct"]), 2),
                flush=True,
            )

        print("\n=== FINAL LOCKED ===", flush=True)
        for label in locked_variants:
            oos = final["oos"][label]["metrics"]
            recent = final["recent"][label]["metrics"]
            full = final["full"][label]["metrics"]
            print(
                label,
                final["full"][label]["allocation"],
                "oos=",
                round(float(oos["cagr_pct"]), 2),
                round(float(oos["mdd_pct"]), 2),
                "recent=",
                round(float(recent["cagr_pct"]), 2),
                round(float(recent["mdd_pct"]), 2),
                "full=",
                round(float(full["cagr_pct"]), 2),
                round(float(full["mdd_pct"]), 2),
                flush=True,
            )
        print(f"saved={args.output}", flush=True)
    finally:
        backtest_engine_module.calculate_score = original_score
        globals()["ACTIVE_ALLOCATION"] = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
