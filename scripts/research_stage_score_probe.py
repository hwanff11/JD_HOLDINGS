#!/usr/bin/env python3
"""Probe whether S2/S3 score floors bind in JDSS V3.1.1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from scripts.research_stage_score_thresholds import (
    ROOT,
    _config_with_thresholds,
    _prepare_frames,
)

PERIODS = {
    "full": ("2011-01-01", "2026-08-12"),
    "recent": ("2022-01-01", "2026-08-12"),
    "oos": ("2023-01-01", "2026-08-12"),
}


def _run_symbol(config, frames, symbol: str, start: str, end: str, slippage: float):
    sector_data = None
    if symbol == "SOXL":
        sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
    return StrategyBacktestEngine(config).run(
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


def _stage_signals(result) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for signal in result.signals:
        stage = signal.get("target_stage")
        if stage not in (2, 3):
            continue
        rows.append(
            {
                "trade_date": signal.get("trade_date"),
                "target_stage": int(stage),
                "score": int(signal.get("score", 0)),
            }
        )
    return rows


def _distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"count": len(rows)}
    for stage in (2, 3):
        scores = [row["score"] for row in rows if row["target_stage"] == stage]
        result[f"stage{stage}"] = {
            "count": len(scores),
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "below_50": sum(score < 50 for score in scores),
            "below_55": sum(score < 55 for score in scores),
            "below_60": sum(score < 60 for score in scores),
            "below_65": sum(score < 65 for score in scores),
        }
    return result


def _max_equity_diff(left, right) -> float:
    common = left.equity_curve.index.intersection(right.equity_curve.index)
    if len(common) == 0:
        return 0.0
    return round(
        float((left.equity_curve.loc[common] - right.equity_curve.loc[common]).abs().max()),
        6,
    )


def _compare(left, right) -> dict[str, Any]:
    return {
        "trades_equal": list(left.trades) == list(right.trades),
        "signals_equal": list(left.signals) == list(right.signals),
        "closed_cycles_equal": list(left.closed_cycles) == list(right.closed_cycles),
        "max_equity_diff": _max_equity_diff(left, right),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/stage_score_probe.json")
    parser.add_argument("--slippage", type=float, default=0.001)
    args = parser.parse_args()

    config = load_config(ROOT / "strategy.yaml")
    frames = _prepare_frames(config, "2026-08-12", refresh=False)
    loose = _config_with_thresholds(config, 55, 45, 45)
    strict = _config_with_thresholds(config, 55, 65, 65)

    report: dict[str, Any] = {
        "strategy_version": config.version,
        "loose": [55, 45, 45],
        "strict": [55, 65, 65],
        "periods": {},
    }
    for period, (start, end) in PERIODS.items():
        period_result: dict[str, Any] = {"symbols": {}}
        for symbol in config.enabled_symbols:
            loose_result = _run_symbol(loose, frames, symbol, start, end, args.slippage)
            strict_result = _run_symbol(strict, frames, symbol, start, end, args.slippage)
            rows = _stage_signals(loose_result)
            period_result["symbols"][symbol] = {
                "loose_stage_signal_distribution": _distribution(rows),
                "loose_stage_signals": rows,
                "loose_vs_strict": _compare(loose_result, strict_result),
            }
        report["periods"][period] = period_result

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
