#!/usr/bin/env python3
"""Focused robustness validation for V3.1.1 baseline 55/55/55 vs candidate 55/60/50."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from scripts import research_stage_score_thresholds as base

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (55, 55, 55)
CANDIDATE = (55, 60, 50)
PERIODS = {
    "full": ("2011-01-01", None),
    "recent": ("2022-01-01", None),
    "oos": ("2023-01-01", None),
}
SLIPPAGES = (0.0005, 0.0010, 0.0020)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)


def _run(
    config,
    frames,
    thresholds: tuple[int, int, int],
    start: str,
    end: str,
    slippage: float,
):
    scenario = base._config_with_thresholds(config, *thresholds)
    boosters = {}
    for symbol in scenario.enabled_symbols:
        sector_data = None
        if symbol == "SOXL":
            sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
        boosters[symbol] = StrategyBacktestEngine(scenario).run(
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
    portfolio = PortfolioBacktestEngine(scenario).run(
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
    return scenario, boosters, portfolio


def _compact_portfolio(portfolio) -> dict[str, Any]:
    metrics = portfolio.metrics
    keys = (
        "total_return_pct",
        "cagr_pct",
        "mdd_pct",
        "sharpe",
        "sortino",
        "average_exposure_pct",
        "maximum_invested_cost",
        "component_fills",
        "annual_returns_pct",
    )
    return {key: _jsonable(metrics[key]) for key in keys if key in metrics}


def _stage_signal_rows(result) -> list[dict[str, Any]]:
    rows = []
    for signal in result.signals:
        stage = int(signal.get("target_stage") or 0)
        if stage not in (2, 3):
            continue
        rows.append(
            {
                "trade_date": str(signal.get("trade_date")),
                "target_stage": stage,
                "score": int(signal.get("score", 0)),
                "action": str(signal.get("action")),
            }
        )
    return rows


def _multiset_diff(left: list[Any], right: list[Any]) -> tuple[list[Any], list[Any]]:
    right_remaining = [_canonical(item) for item in right]
    left_only: list[Any] = []
    for item in left:
        key = _canonical(item)
        if key in right_remaining:
            right_remaining.remove(key)
        else:
            left_only.append(_jsonable(item))

    left_remaining = [_canonical(item) for item in left]
    right_only: list[Any] = []
    for item in right:
        key = _canonical(item)
        if key in left_remaining:
            left_remaining.remove(key)
        else:
            right_only.append(_jsonable(item))
    return left_only, right_only


def _economic_diff(baseline_result, candidate_result) -> dict[str, Any]:
    baseline_trades = list(baseline_result.trades)
    candidate_trades = list(candidate_result.trades)
    baseline_only, candidate_only = _multiset_diff(baseline_trades, candidate_trades)

    baseline_cycles = list(baseline_result.closed_cycles)
    candidate_cycles = list(candidate_result.closed_cycles)
    baseline_cycles_only, candidate_cycles_only = _multiset_diff(baseline_cycles, candidate_cycles)

    baseline_signals = _stage_signal_rows(baseline_result)
    candidate_signals = _stage_signal_rows(candidate_result)
    baseline_signals_only, candidate_signals_only = _multiset_diff(
        baseline_signals,
        candidate_signals,
    )
    return {
        "trades_equal": not baseline_only and not candidate_only,
        "baseline_only_trades": baseline_only,
        "candidate_only_trades": candidate_only,
        "changed_trade_count": len(baseline_only) + len(candidate_only),
        "baseline_only_cycles": baseline_cycles_only,
        "candidate_only_cycles": candidate_cycles_only,
        "changed_cycle_records": len(baseline_cycles_only) + len(candidate_cycles_only),
        "baseline_only_stage_signals": baseline_signals_only,
        "candidate_only_stage_signals": candidate_signals_only,
        "changed_stage_signal_count": len(baseline_signals_only) + len(candidate_signals_only),
    }


def _metric_delta(baseline_metrics: dict[str, Any], candidate_metrics: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key in (
        "total_return_pct",
        "cagr_pct",
        "mdd_pct",
        "sharpe",
        "sortino",
        "average_exposure_pct",
        "maximum_invested_cost",
    ):
        if key in baseline_metrics and key in candidate_metrics:
            result[key] = round(float(candidate_metrics[key]) - float(baseline_metrics[key]), 6)
    return result


def _annual_delta(baseline_metrics: dict[str, Any], candidate_metrics: dict[str, Any]) -> dict[str, Any]:
    baseline = baseline_metrics.get("annual_returns_pct", {})
    candidate = candidate_metrics.get("annual_returns_pct", {})
    years = sorted(set(baseline) | set(candidate), key=str)
    rows = {}
    for year in years:
        left = float(baseline.get(year, 0.0))
        right = float(candidate.get(year, 0.0))
        rows[str(year)] = {
            "baseline": round(left, 4),
            "candidate": round(right, 4),
            "delta_pct_point": round(right - left, 4),
        }
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "strategy.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/stage_candidate_validation.json",
    )
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    end = MarketClock().latest_completed_session().isoformat()
    frames = base._prepare_frames(config, end, args.refresh)

    report: dict[str, Any] = {
        "strategy_version": config.version,
        "research_end": end,
        "baseline": list(BASELINE),
        "candidate": list(CANDIDATE),
        "selection_warning": (
            "Candidate 55/60/50 became interesting after viewing 2023+ OOS in the broad grid. "
            "Therefore this validation is robustness evidence, not a fresh untouched holdout."
        ),
        "slippage_stress": {},
        "exact_full_diff_at_10bp": {},
    }

    for slippage in SLIPPAGES:
        label = f"{slippage * 10000:.0f}bp"
        report["slippage_stress"][label] = {}
        for period, (start, period_end) in PERIODS.items():
            resolved_end = period_end or end
            _, baseline_boosters, baseline_portfolio = _run(
                config,
                frames,
                BASELINE,
                start,
                resolved_end,
                slippage,
            )
            _, candidate_boosters, candidate_portfolio = _run(
                config,
                frames,
                CANDIDATE,
                start,
                resolved_end,
                slippage,
            )
            baseline_metrics = _compact_portfolio(baseline_portfolio)
            candidate_metrics = _compact_portfolio(candidate_portfolio)
            report["slippage_stress"][label][period] = {
                "baseline": baseline_metrics,
                "candidate": candidate_metrics,
                "delta": _metric_delta(baseline_metrics, candidate_metrics),
                "annual_delta": _annual_delta(baseline_metrics, candidate_metrics),
            }

            if slippage == 0.001 and period == "full":
                report["exact_full_diff_at_10bp"] = {
                    symbol: _economic_diff(
                        baseline_boosters[symbol],
                        candidate_boosters[symbol],
                    )
                    for symbol in config.enabled_symbols
                }

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 55/55/55 vs 55/60/50 ROBUSTNESS ===")
    for label, periods in report["slippage_stress"].items():
        print(f"\n{label}")
        for period, values in periods.items():
            baseline_metrics = values["baseline"]
            candidate_metrics = values["candidate"]
            print(
                period,
                f"base CAGR/MDD={baseline_metrics['cagr_pct']:.2f}/{baseline_metrics['mdd_pct']:.2f}",
                f"cand={candidate_metrics['cagr_pct']:.2f}/{candidate_metrics['mdd_pct']:.2f}",
                f"Sharpe={baseline_metrics['sharpe']:.3f}->{candidate_metrics['sharpe']:.3f}",
            )
    print("\n=== EXACT FULL DIFF 10bp ===")
    for symbol, values in report["exact_full_diff_at_10bp"].items():
        print(
            symbol,
            f"changed_trades={values['changed_trade_count']}",
            f"changed_cycles={values['changed_cycle_records']}",
            f"changed_stage_signals={values['changed_stage_signal_count']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
