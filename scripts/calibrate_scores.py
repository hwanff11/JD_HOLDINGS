#!/usr/bin/env python3
"""Recalibrate JDSS component curves without changing component maxima or grades.

Selection intentionally stops at 2022-12-30. The OOS command must be invoked
separately after one candidate has been selected from train/validation results.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.indicators import (
    calculate_indicators,
    normalize_ohlcv,
    snapshot_from_row,
)
from jd_holdings.core.regime import evaluate_regime

ROOT = Path(__file__).resolve().parents[1]
DATA_END = "2026-08-04"
SPLITS = {
    "train": ("2011-01-01", "2018-12-31"),
    "validation": ("2019-01-01", "2022-12-30"),
    "oos": ("2023-01-01", DATA_END),
}
SYMBOLS = ("TQQQ", "SOXL")


def _parse_floats(value: str) -> tuple[float, ...]:
    return tuple(float(item.strip()) for item in value.split(",") if item.strip())


def _load_frame(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "cache" / f"{symbol}_2011-01-01_{DATA_END}_adjusted.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"cached adjusted data is required: {path}\n"
            "Run: jdss backtest --symbol ALL --start 2011-01-01 --refresh"
        )
    return normalize_ohlcv(pd.read_csv(path, index_col=0, parse_dates=True))


def _calibrated_config(
    base: StrategyConfig,
    oversold: float,
    reversal: float,
    volume: float,
    atr: float,
) -> StrategyConfig:
    scoring = {
        **base.scoring,
        "calibration": {
            "enabled": True,
            "method": "power",
            "exponents": {
                "regime": 1.0,
                "oversold": oversold,
                "reversal": reversal,
                "volume": volume,
                "atr": atr,
            },
        },
    }
    return replace(base, version="JDSS-1.2.0-research", scoring=scoring)


def _prepare(base: StrategyConfig) -> dict[str, Any]:
    frames = {
        symbol: calculate_indicators(_load_frame(symbol), base)
        for symbol in ("SPY", "QQQ", *SYMBOLS)
    }
    common_index = frames["SPY"].index.intersection(frames["QQQ"].index)
    for symbol in SYMBOLS:
        common_index = common_index.intersection(frames[symbol].index)
    required = [
        "cci5",
        "cci10",
        "rsi5",
        "rsi14",
        "ema5",
        "ema20",
        "ema60",
        "bb_lower",
        "atr14",
        "atr_pct",
        "volume_ratio",
        "close_position",
        "previous_close",
    ]
    valid = pd.Series(True, index=common_index)
    for frame in frames.values():
        valid &= frame.loc[common_index, required].notna().all(axis=1)
    common_index = common_index[valid.to_numpy()]
    snapshots = {
        symbol: {
            timestamp: snapshot_from_row(symbol, timestamp, frames[symbol].loc[timestamp])
            for timestamp in common_index
        }
        for symbol in ("SPY", "QQQ", *SYMBOLS)
    }
    regimes = {
        timestamp: evaluate_regime(snapshots["SPY"][timestamp], snapshots["QQQ"][timestamp])
        for timestamp in common_index
    }
    return {"frames": frames, "snapshots": snapshots, "regimes": regimes}


def _combined_metrics(results: dict[str, Any]) -> dict[str, float | int]:
    equity = pd.concat(
        [result.equity_curve.rename(symbol) for symbol, result in results.items()],
        axis=1,
        join="inner",
    ).sum(axis=1)
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    total_return = final / initial - 1
    cagr = (final / initial) ** (1 / years) - 1
    sharpe, sortino = risk_adjusted_metrics(equity, 252)
    return {
        "total_return_pct": round(total_return * 100, 4),
        "cagr_pct": round(cagr * 100, 4),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "signals": sum(int(result.metrics["signals"]) for result in results.values()),
        "executed_entries": sum(
            int(result.metrics["executed_entries"]) for result in results.values()
        ),
        "closed_cycles": sum(int(result.metrics["closed_cycles"]) for result in results.values()),
    }


def _evaluate_segment(
    config: StrategyConfig,
    prepared: dict[str, Any],
    start: str,
    end: str,
    slippage: float,
) -> tuple[dict[str, float | int], dict[str, dict[str, float | int]]]:
    engine = BacktestEngine(config)
    results = {
        symbol: engine.run(
            symbol,
            prepared["frames"][symbol],
            prepared["frames"]["SPY"],
            prepared["frames"]["QQQ"],
            start=start,
            end=end,
            slippage=slippage,
            indicators_precomputed=True,
            snapshots_precomputed=prepared["snapshots"],
            regimes_precomputed=prepared["regimes"],
        )
        for symbol in SYMBOLS
    }
    per_symbol = {symbol: dict(result.metrics) for symbol, result in results.items()}
    return _combined_metrics(results), per_symbol


def _flatten_metrics(prefix: str, metrics: dict[str, float | int]) -> dict[str, float | int]:
    return {f"{prefix}_{key}": value for key, value in metrics.items()}


def _selection_objective(row: dict[str, Any]) -> float:
    """Reward return that persists across both development segments."""
    train_cagr = float(row["train_cagr_pct"])
    validation_cagr = float(row["validation_cagr_pct"])
    weakest_cagr = min(train_cagr, validation_cagr)
    instability = abs(train_cagr - validation_cagr)
    worst_mdd = abs(min(float(row["train_mdd_pct"]), float(row["validation_mdd_pct"])))
    weakest_sharpe = min(float(row["train_sharpe"]), float(row["validation_sharpe"]))
    return (
        weakest_cagr
        + 0.35 * validation_cagr
        - 0.20 * instability
        - 0.05 * worst_mdd
        + 0.15 * weakest_sharpe
    )


def select(args: argparse.Namespace) -> int:
    base = load_config(args.config)
    prepared = _prepare(base)
    combinations = list(itertools.product(args.oversold, args.reversal, args.volume, args.atr))
    rows: list[dict[str, Any]] = []
    for index, (oversold, reversal, volume, atr) in enumerate(combinations, start=1):
        config = _calibrated_config(base, oversold, reversal, volume, atr)
        row: dict[str, Any] = {
            "oversold_exponent": oversold,
            "reversal_exponent": reversal,
            "volume_exponent": volume,
            "atr_exponent": atr,
        }
        for name in ("train", "validation"):
            start, end = SPLITS[name]
            combined, per_symbol = _evaluate_segment(config, prepared, start, end, args.slippage)
            row.update(_flatten_metrics(name, combined))
            for symbol, metrics in per_symbol.items():
                row.update(_flatten_metrics(f"{name}_{symbol.lower()}", metrics))
        row["objective"] = round(_selection_objective(row), 6)
        rows.append(row)
        if index % 25 == 0 or index == len(combinations):
            print(f"evaluated {index}/{len(combinations)}", flush=True)

    frame = pd.DataFrame(rows)
    eligible = (
        (frame["train_total_return_pct"] > 0)
        & (frame["validation_total_return_pct"] > 0)
        & (frame["train_executed_entries"] >= args.minimum_entries)
        & (frame["validation_executed_entries"] >= args.minimum_entries)
    )
    frame["eligible"] = eligible
    frame["validation_return_rank"] = frame["validation_total_return_pct"].rank(
        ascending=False, method="min"
    )
    frame["robust_rank"] = (
        frame["objective"].where(eligible, -math.inf).rank(ascending=False, method="min")
    )
    frame = frame.sort_values(
        ["eligible", "objective", "validation_total_return_pct"],
        ascending=[False, False, False],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    columns = [
        "oversold_exponent",
        "reversal_exponent",
        "volume_exponent",
        "atr_exponent",
        "objective",
        "eligible",
        "train_total_return_pct",
        "train_cagr_pct",
        "train_mdd_pct",
        "train_executed_entries",
        "validation_total_return_pct",
        "validation_cagr_pct",
        "validation_mdd_pct",
        "validation_executed_entries",
    ]
    print(frame.loc[:, columns].head(args.show).to_string(index=False))
    print(f"saved={args.output}")
    return 0


def oos(args: argparse.Namespace) -> int:
    base = load_config(args.config)
    prepared = _prepare(base)
    config = _calibrated_config(
        base,
        args.oversold,
        args.reversal,
        args.volume,
        args.atr,
    )
    report: dict[str, Any] = {
        "candidate": config.scoring["calibration"]["exponents"],
        "split": {"name": "oos", "start": SPLITS["oos"][0], "end": SPLITS["oos"][1]},
        "stress": {},
    }
    for slippage in args.slippages:
        combined, per_symbol = _evaluate_segment(config, prepared, *SPLITS["oos"], slippage)
        report["stress"][str(slippage)] = {
            "combined": combined,
            "symbols": per_symbol,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved={args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    selection = subparsers.add_parser("select", help="search train/validation only")
    selection.add_argument(
        "--oversold", type=_parse_floats, default=_parse_floats("0.35,0.45,0.55,0.65,0.75")
    )
    selection.add_argument(
        "--reversal", type=_parse_floats, default=_parse_floats("0.35,0.5,0.65,0.8,1.0")
    )
    selection.add_argument("--volume", type=_parse_floats, default=_parse_floats("0.4,0.6,0.8,1.0"))
    selection.add_argument("--atr", type=_parse_floats, default=_parse_floats("0.5,0.75,1.0"))
    selection.add_argument("--slippage", type=float, default=0.001)
    selection.add_argument("--minimum-entries", type=int, default=4)
    selection.add_argument("--show", type=int, default=20)
    selection.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "score_calibration_selection.csv"
    )
    selection.set_defaults(handler=select)

    holdout = subparsers.add_parser("oos", help="evaluate one locked candidate from 2023")
    holdout.add_argument("--oversold", type=float, required=True)
    holdout.add_argument("--reversal", type=float, required=True)
    holdout.add_argument("--volume", type=float, required=True)
    holdout.add_argument("--atr", type=float, required=True)
    holdout.add_argument(
        "--slippages", type=_parse_floats, default=_parse_floats("0.001,0.003,0.005")
    )
    holdout.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "score_calibration_oos.json"
    )
    holdout.set_defaults(handler=oos)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
