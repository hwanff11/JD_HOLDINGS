#!/usr/bin/env python3
"""Prove or falsify ATR-score redundancy in JDSS V3.1.1.

Compare production scoring (ATR included, score floor 55) with an ATR-free score
(total minus calibrated ATR component, score floor 50). Production hard gates and
all other trading rules remain unchanged. The report also measures ATR-score
frequency on all valid dates, hard-gate-eligible dates, threshold-eligible dates,
and actual strategy signal dates.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import jd_holdings.backtest.engine as backtest_engine_module
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.enums import MarketRegime
from jd_holdings.core.indicators import calculate_indicators, snapshot_from_row
from jd_holdings.core.models import ScoreResult
from jd_holdings.core.regime import evaluate_regime
from jd_holdings.core.scoring import calculate_grade
from jd_holdings.core.scoring import calculate_score as production_calculate_score
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
PERIODS = {
    "full": "2011-01-01",
    "recent": "2022-01-01",
    "oos": "2023-01-01",
}
REQUIRED_COLUMNS = [
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


def _atr_free_score(snapshot, regime, config) -> ScoreResult:
    base = production_calculate_score(snapshot, regime, config)
    total = max(0, int(base.total) - int(base.atr_score))
    return ScoreResult(
        total=total,
        grade=calculate_grade(total, config),
        regime=base.regime,
        regime_score=base.regime_score,
        oversold_score=base.oversold_score,
        reversal_score=base.reversal_score,
        volume_score=base.volume_score,
        atr_score=base.atr_score,
        raw_regime_score=base.raw_regime_score,
        raw_oversold_score=base.raw_oversold_score,
        raw_reversal_score=base.raw_reversal_score,
        raw_volume_score=base.raw_volume_score,
        raw_atr_score=base.raw_atr_score,
    )


def _config_with_floor(config: StrategyConfig, floor: int) -> StrategyConfig:
    global_config = replace(config.global_, entry_score=floor)
    stages = {
        stage: replace(rule, min_score=floor)
        for stage, rule in config.additional_entry.stages.items()
    }
    return replace(
        config,
        global_=global_config,
        additional_entry=replace(config.additional_entry, stages=stages),
    )


def _prepare_frames(config: StrategyConfig, end: str, refresh: bool) -> dict[str, pd.DataFrame]:
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = "2009-11-27"
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    raw = {
        symbol: source.daily(symbol, warmup_start, end, refresh=refresh)
        for symbol in symbols
    }
    return {
        symbol: calculate_indicators(frame, config)
        for symbol, frame in raw.items()
    }


def _run_boosters(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    start: str,
    end: str,
    slippage: float,
    scoring_fn: Callable[..., ScoreResult],
) -> dict[str, Any]:
    original = backtest_engine_module.calculate_score
    backtest_engine_module.calculate_score = scoring_fn
    try:
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
    finally:
        backtest_engine_module.calculate_score = original


def _run_portfolio(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    boosters: dict[str, Any],
    start: str,
    end: str,
    slippage: float,
):
    portfolio_frames = {
        "TQQQ": frames["TQQQ"],
        "SOXL": frames["SOXL"],
        "QQQ": frames["QQQ"],
        "SOXX": frames["SOXX"],
    }
    return PortfolioBacktestEngine(config).run(
        portfolio_frames,
        boosters,
        start=start,
        end=end,
        slippage=slippage,
    )


def _signal_identity(signal: dict[str, Any]) -> tuple[Any, ...]:
    return (
        signal.get("trade_date"),
        signal.get("action"),
        signal.get("target_stage"),
        round(float(signal.get("signal_close", 0.0)), 8),
        round(float(signal.get("planned_budget", 0.0)), 4),
    )


def _trade_identity(trade: dict[str, Any]) -> str:
    return json.dumps(trade, sort_keys=True, ensure_ascii=False, default=str)


def _metric_subset(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "total_return_pct",
        "cagr_pct",
        "mdd_pct",
        "sharpe",
        "sortino",
        "average_exposure_pct",
        "signals",
        "executed_entries",
        "closed_cycles",
        "component_fills",
        "maximum_invested_cost",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def _series_max_abs_diff(left: pd.Series, right: pd.Series) -> float:
    common = left.index.intersection(right.index)
    if len(common) != len(left) or len(common) != len(right):
        return float("inf")
    return float((left.loc[common] - right.loc[common]).abs().max())


def _compare_period(
    base_config: StrategyConfig,
    atr_free_config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    start: str,
    end: str,
    slippage: float,
) -> dict[str, Any]:
    baseline = _run_boosters(
        base_config,
        frames,
        start,
        end,
        slippage,
        production_calculate_score,
    )
    candidate = _run_boosters(
        atr_free_config,
        frames,
        start,
        end,
        slippage,
        _atr_free_score,
    )
    symbol_comparisons: dict[str, Any] = {}
    for symbol in base_config.enabled_symbols:
        base = baseline[symbol]
        alt = candidate[symbol]
        base_signals = [_signal_identity(item) for item in base.signals]
        alt_signals = [_signal_identity(item) for item in alt.signals]
        base_trades = [_trade_identity(item) for item in base.trades]
        alt_trades = [_trade_identity(item) for item in alt.trades]
        base_cycles = [_trade_identity(item) for item in base.closed_cycles]
        alt_cycles = [_trade_identity(item) for item in alt.closed_cycles]
        symbol_comparisons[symbol] = {
            "baseline_metrics": _metric_subset(base.metrics),
            "atr_free_metrics": _metric_subset(alt.metrics),
            "signal_identity_equal": base_signals == alt_signals,
            "trade_identity_equal": base_trades == alt_trades,
            "closed_cycles_equal": base_cycles == alt_cycles,
            "equity_max_abs_diff": _series_max_abs_diff(base.equity_curve, alt.equity_curve),
            "baseline_signal_count": len(base_signals),
            "atr_free_signal_count": len(alt_signals),
            "baseline_trade_count": len(base_trades),
            "atr_free_trade_count": len(alt_trades),
            "baseline_only_signals": [
                item for item in base_signals if item not in set(alt_signals)
            ][:25],
            "atr_free_only_signals": [
                item for item in alt_signals if item not in set(base_signals)
            ][:25],
            "baseline_only_trades": [
                item for item in base_trades if item not in set(alt_trades)
            ][:25],
            "atr_free_only_trades": [
                item for item in alt_trades if item not in set(base_trades)
            ][:25],
        }

    base_portfolio = _run_portfolio(
        base_config, frames, baseline, start, end, slippage
    )
    alt_portfolio = _run_portfolio(
        atr_free_config, frames, candidate, start, end, slippage
    )
    base_portfolio_trades = [_trade_identity(item) for item in base_portfolio.trades]
    alt_portfolio_trades = [_trade_identity(item) for item in alt_portfolio.trades]
    portfolio_comparison = {
        "baseline_metrics": _metric_subset(base_portfolio.metrics),
        "atr_free_metrics": _metric_subset(alt_portfolio.metrics),
        "trade_identity_equal": base_portfolio_trades == alt_portfolio_trades,
        "equity_max_abs_diff": _series_max_abs_diff(
            base_portfolio.equity_curve, alt_portfolio.equity_curve
        ),
        "baseline_trade_count": len(base_portfolio_trades),
        "atr_free_trade_count": len(alt_portfolio_trades),
        "baseline_only_trades": [
            item for item in base_portfolio_trades if item not in set(alt_portfolio_trades)
        ][:25],
        "atr_free_only_trades": [
            item for item in alt_portfolio_trades if item not in set(base_portfolio_trades)
        ][:25],
    }
    strict = bool(portfolio_comparison["trade_identity_equal"])
    strict = strict and portfolio_comparison["equity_max_abs_diff"] <= 1e-8
    for comparison in symbol_comparisons.values():
        strict = strict and bool(comparison["signal_identity_equal"])
        strict = strict and bool(comparison["trade_identity_equal"])
        strict = strict and bool(comparison["closed_cycles_equal"])
        strict = strict and comparison["equity_max_abs_diff"] <= 1e-8
    return {
        "strict_equivalence": strict,
        "symbols": symbol_comparisons,
        "portfolio": portfolio_comparison,
        "baseline_results": baseline,
    }


def _counter(values: list[int]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(Counter(values).items())}


def _score_distribution(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    end: str,
    baseline_full: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for symbol in config.enabled_symbols:
        target = frames[symbol]
        common = target.index.intersection(frames["SPY"].index).intersection(frames["QQQ"].index)
        common = common[(common >= pd.Timestamp("2011-01-01")) & (common <= pd.Timestamp(end))]
        valid = (
            target.loc[common, REQUIRED_COLUMNS].notna().all(axis=1)
            & frames["SPY"].loc[common, REQUIRED_COLUMNS].notna().all(axis=1)
            & frames["QQQ"].loc[common, REQUIRED_COLUMNS].notna().all(axis=1)
        )
        common = common[valid]
        signal_dates = {
            str(signal["trade_date"]) for signal in baseline_full[symbol].signals
        }
        all_atr: list[int] = []
        hard_atr: list[int] = []
        threshold_atr: list[int] = []
        signal_atr: list[int] = []
        signal_raw_atr: list[int] = []
        signal_atr_pct: list[float] = []
        threshold_mismatches: list[dict[str, Any]] = []
        non_five_signal_dates: list[dict[str, Any]] = []

        for timestamp in common:
            snapshot = snapshot_from_row(symbol, timestamp, target.loc[timestamp])
            spy_snapshot = snapshot_from_row("SPY", timestamp, frames["SPY"].loc[timestamp])
            qqq_snapshot = snapshot_from_row("QQQ", timestamp, frames["QQQ"].loc[timestamp])
            regime = evaluate_regime(spy_snapshot, qqq_snapshot)
            score = production_calculate_score(snapshot, regime, config)
            atr_score = int(score.atr_score)
            no_atr_total = int(score.total) - atr_score
            all_atr.append(atr_score)
            hard_gate = (
                regime != MarketRegime.RED
                and int(score.reversal_score) >= config.global_.minimum_reversal_score
            )
            if hard_gate:
                hard_atr.append(atr_score)
            baseline_pass = hard_gate and int(score.total) >= 55
            atr_free_pass = hard_gate and no_atr_total >= 50
            if baseline_pass:
                threshold_atr.append(atr_score)
            if baseline_pass != atr_free_pass:
                threshold_mismatches.append(
                    {
                        "date": timestamp.date().isoformat(),
                        "regime": regime.value,
                        "total": int(score.total),
                        "atr_score": atr_score,
                        "raw_atr_score": int(score.raw_atr_score),
                        "atr_pct": round(float(snapshot.atr_pct), 6),
                        "atr_free_total": no_atr_total,
                        "baseline_pass": baseline_pass,
                        "atr_free_pass": atr_free_pass,
                    }
                )
            if timestamp.date().isoformat() in signal_dates:
                signal_atr.append(atr_score)
                signal_raw_atr.append(int(score.raw_atr_score))
                signal_atr_pct.append(float(snapshot.atr_pct))
                if atr_score != 5:
                    non_five_signal_dates.append(
                        {
                            "date": timestamp.date().isoformat(),
                            "regime": regime.value,
                            "total": int(score.total),
                            "atr_score": atr_score,
                            "raw_atr_score": int(score.raw_atr_score),
                            "atr_pct": round(float(snapshot.atr_pct), 6),
                            "atr_free_total": no_atr_total,
                        }
                    )

        result[symbol] = {
            "valid_dates": len(common),
            "actual_signal_dates": len(signal_dates),
            "atr_score_all_dates": _counter(all_atr),
            "atr_score_hard_gate_dates": _counter(hard_atr),
            "atr_score_threshold_eligible_dates": _counter(threshold_atr),
            "atr_score_actual_signal_dates": _counter(signal_atr),
            "raw_atr_score_actual_signal_dates": _counter(signal_raw_atr),
            "actual_signal_atr_pct_min": round(min(signal_atr_pct), 6) if signal_atr_pct else None,
            "actual_signal_atr_pct_max": round(max(signal_atr_pct), 6) if signal_atr_pct else None,
            "actual_signals_with_atr_not_5": non_five_signal_dates,
            "threshold_equivalence_mismatch_count": len(threshold_mismatches),
            "threshold_equivalence_mismatches": threshold_mismatches[:100],
        }
    return result


def _json_ready_period(period: dict[str, Any]) -> dict[str, Any]:
    return {
        "strict_equivalence": period["strict_equivalence"],
        "symbols": period["symbols"],
        "portfolio": period["portfolio"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/atr_redundancy.json",
    )
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    atr_free_config = _config_with_floor(config, 50)
    end = MarketClock().latest_completed_session().isoformat()
    frames = _prepare_frames(config, end, args.refresh)

    period_results: dict[str, Any] = {}
    for label, start in PERIODS.items():
        print(f"running {label}: {start} -> {end}", flush=True)
        period_results[label] = _compare_period(
            config,
            atr_free_config,
            frames,
            start,
            end,
            args.slippage,
        )

    distribution = _score_distribution(
        config,
        frames,
        end,
        period_results["full"]["baseline_results"],
    )
    strict_all_periods = all(
        period_results[label]["strict_equivalence"] for label in PERIODS
    )
    report = {
        "strategy_version": config.version,
        "config_version": config.config_version,
        "research_end": end,
        "slippage": args.slippage,
        "baseline": {
            "atr_score": "included",
            "s1_s2_s3_score_floor": 55,
        },
        "candidate": {
            "atr_score": "removed from total only",
            "s1_s2_s3_score_floor": 50,
            "hard_gates": "unchanged",
        },
        "strict_equivalence_all_periods": strict_all_periods,
        "periods": {
            label: _json_ready_period(period_results[label])
            for label in PERIODS
        },
        "atr_distribution": distribution,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print("\n=== ATR DISTRIBUTION ON ACTUAL SIGNAL DATES ===")
    for symbol, values in distribution.items():
        print(
            symbol,
            "signals=",
            values["actual_signal_dates"],
            "atr=",
            values["atr_score_actual_signal_dates"],
            "non5=",
            len(values["actual_signals_with_atr_not_5"]),
            "threshold_mismatches=",
            values["threshold_equivalence_mismatch_count"],
        )
    print("\n=== EXACT EQUIVALENCE ===")
    for label, period in period_results.items():
        portfolio = period["portfolio"]
        print(
            label,
            "strict=",
            period["strict_equivalence"],
            "portfolio_trade_equal=",
            portfolio["trade_identity_equal"],
            "equity_diff=",
            portfolio["equity_max_abs_diff"],
        )
        for symbol, values in period["symbols"].items():
            print(
                " ",
                symbol,
                "signals_equal=",
                values["signal_identity_equal"],
                "trades_equal=",
                values["trade_identity_equal"],
                "equity_diff=",
                values["equity_max_abs_diff"],
            )
    print(f"saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
