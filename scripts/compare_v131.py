#!/usr/bin/env python3
"""Compare JDSS v1.3.0, v1.3.1, and v1.3.1 without score calibration."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")


def _variants(base: StrategyConfig) -> dict[str, StrategyConfig]:
    v130_calibration = {
        "enabled": True,
        "method": "power",
        "exponents": {
            "regime": 1.0,
            "oversold": 0.10,
            "reversal": 0.125,
            "volume": 0.025,
            "atr": 1.0,
        },
    }
    v130 = replace(
        base,
        version="JDSS-1.3.0-comparable",
        config_version="1.2.0-comparable",
        global_=replace(base.global_, minimum_reversal_score=0),
        market_regime={
            key: value for key, value in base.market_regime.items() if key != "soxl_sector_guard"
        },
        scoring={**base.scoring, "calibration": v130_calibration},
    )
    calibration_off = replace(
        base,
        version="JDSS-1.3.1-calibration-off",
        scoring={
            **base.scoring,
            "calibration": {**base.scoring["calibration"], "enabled": False},
        },
    )
    return {
        "v1.3.0": v130,
        "v1.3.1": base,
        "v1.3.1_calibration_off": calibration_off,
    }


def _combined_metrics(results: dict[str, BacktestResult], annualization_days: int) -> dict[str, Any]:
    equity = pd.concat(
        [result.equity_curve.rename(symbol) for symbol, result in results.items()],
        axis=1,
        join="inner",
    ).sum(axis=1)
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    elapsed_years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    total_return = final / initial - 1
    cagr = (final / initial) ** (1 / elapsed_years) - 1
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    yearly_returns = {
        str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
        for year, group in equity.groupby(equity.index.year)
    }
    return {
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "closed_cycles": sum(int(result.metrics["closed_cycles"]) for result in results.values()),
        "signals": sum(int(result.metrics["signals"]) for result in results.values()),
        "executed_entries": sum(
            int(result.metrics["executed_entries"]) for result in results.values()
        ),
        "average_capital_utilization_pct": round(
            sum(float(result.metrics["average_capital_utilization_pct"]) for result in results.values())
            / len(results),
            2,
        ),
        "annual_returns_pct": yearly_returns,
    }


def _variant_settings(config: StrategyConfig) -> dict[str, Any]:
    guard = config.market_regime.get("soxl_sector_guard", {})
    return {
        "minimum_reversal_score": config.global_.minimum_reversal_score,
        "calibration": config.scoring["calibration"],
        "soxl_sector_guard_enabled": bool(guard.get("enabled", False)),
        "soxl_sector_guard_stages": list(guard.get("blocked_stages", ())),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    base = load_config(args.config)
    variants = _variants(base)
    warmup_start = (datetime.fromisoformat(args.full_start).date() - timedelta(days=400)).isoformat()
    source = YFinanceDataSource(args.cache_dir)
    frames = {
        symbol: source.daily(symbol, warmup_start, args.end, refresh=args.refresh)
        for symbol in (*SYMBOLS, *BENCHMARKS)
    }
    segments = {
        "validation_2021_2024": ("2021-01-01", "2024-12-31"),
        "full_history": (args.full_start, args.end),
    }
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "engine_commit": args.commit,
        "comparison_method": (
            "Current corrected engine for all candidates; only strategy settings differ. "
            "Capital is fixed at USD 10,000 per symbol without cross-symbol reinvestment."
        ),
        "common_assumptions": {
            "symbols": list(SYMBOLS),
            "capital_per_symbol_usd": float(base.global_.capital_per_symbol),
            "buy_fee": float(base.global_.buy_fee),
            "sell_fee": float(base.global_.sell_fee),
            "slippage": args.slippage,
            "data_source": "yfinance adjusted daily OHLCV",
            "execution": "signal close, next-session open subject to chase/limit rules",
        },
        "segments": {},
    }
    for segment_name, (start, end) in segments.items():
        segment: dict[str, Any] = {"start": start, "end": end, "variants": {}}
        for variant_name, config in variants.items():
            engine = BacktestEngine(config)
            results = {
                symbol: engine.run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=args.slippage,
                    sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None,
                )
                for symbol in SYMBOLS
            }
            segment["variants"][variant_name] = {
                "settings": _variant_settings(config),
                "combined": _combined_metrics(results, config.backtest.annualization_days),
                "symbols": {
                    symbol: result.to_dict(include_equity=False)
                    for symbol, result in results.items()
                },
            }
            combined = segment["variants"][variant_name]["combined"]
            print(
                f"{segment_name} {variant_name}: "
                f"return={combined['total_return_pct']:+.2f}% "
                f"CAGR={combined['cagr_pct']:+.2f}% "
                f"MDD={combined['mdd_pct']:.2f}% "
                f"cycles={combined['closed_cycles']}"
            )
        report["segments"][segment_name] = segment
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "cache")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-start", default="2011-01-01")
    parser.add_argument("--end", default="2026-08-04")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--refresh", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
