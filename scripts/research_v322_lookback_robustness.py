#!/usr/bin/env python3
"""Robustness check for the frozen RS one-way structure around a 6-month lookback.

This is not a parameter search. It perturbs the frozen 126-session relative-strength
lookback by plus/minus one trading month and checks whether the strategy's properties
remain similar.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
RS_SCRIPT = ROOT / "scripts" / "research_v321_relative_strength_booster.py"
SPLIT_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_split.py"
MONTHLY_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_monthly_lock.py"
ONEWAY_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_oneway_exit.py"
START = "2011-01-03"
LOOKBACKS = (105, 126, 147)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def add_return(frame: pd.DataFrame, lookback: int) -> pd.DataFrame:
    result = frame.copy()
    result[f"ret{lookback}"] = result["close"].astype(float).pct_change(lookback)
    return result


def make_signal(lookback: int):
    column = f"ret{lookback}"

    def signal(qqq_row: pd.Series, soxx_row: pd.Series) -> bool:
        q_value = qqq_row.get(column)
        s_value = soxx_row.get(column)
        if pd.isna(q_value) or pd.isna(s_value):
            return False
        q_ret = float(q_value)
        s_ret = float(s_value)
        return s_ret > 0.0 and s_ret > q_ret

    return signal


def run_lookback(oneway, monthly, rs, base, frames, active, end, fee, slippage, lookback):
    original_signal = monthly.soxx_wins_6m
    monthly.soxx_wins_6m = make_signal(lookback)
    try:
        return oneway.simulate(rs, monthly, base, frames, active, end, fee, slippage)
    finally:
        monthly.soxx_wins_6m = original_signal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_module("rs_booster_lookback", RS_SCRIPT)
    split = load_module("rs_split_lookback", SPLIT_SCRIPT)
    monthly = load_module("rs_monthly_lookback", MONTHLY_SCRIPT)
    oneway = load_module("rs_oneway_lookback", ONEWAY_SCRIPT)
    base = rs.load_base()
    config = load_config(ROOT / "strategy.yaml")
    end = args.end or datetime.now(UTC).date().isoformat()
    warmup = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=420)
    ).isoformat()
    source = YFinanceDataSource(ROOT / "data" / "cache")
    raw = {
        symbol: source.daily(symbol, warmup, end)
        for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    }

    qqq_features = base.features(raw["QQQ"], raw["SPY"])
    soxx_features = rs.momentum_features(raw["SOXX"])
    for lookback in LOOKBACKS:
        qqq_features = add_return(qqq_features, lookback)
        soxx_features = add_return(soxx_features, lookback)

    frames = {
        "QQQ": qqq_features,
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
        "SOXX": soxx_features,
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = base.build_active(config, raw, end, slippage)
    qqq = base.benchmark(raw["QQQ"], START, end, fee, slippage)

    base_policy = rs.BoosterPolicy("BASE_TQQQ", "base")
    base_curve, base_metrics, _ = rs.simulate(
        base, base_policy, frames, active, end, fee, slippage
    )

    results: dict[str, object] = {}
    curves: dict[int, pd.Series] = {}
    metrics_by_lookback: dict[int, dict] = {}
    for lookback in LOOKBACKS:
        curve, metrics, state = run_lookback(
            oneway,
            monthly,
            rs,
            base,
            frames,
            active,
            end,
            fee,
            slippage,
            lookback,
        )
        curves[lookback] = curve
        metrics_by_lookback[lookback] = metrics
        results[str(lookback)] = {
            "metrics": metrics,
            "rolling_3y": split.rolling_distribution(curve, qqq, 3),
            "rolling_5y": split.rolling_distribution(curve, qqq, 5),
            "soxl_sleeve_days_pct_by_year": rs.yearly_soxl_share(state),
        }

    periods = {
        "2011_2014": ("2011-01-03", "2014-12-31"),
        "2015_2018": ("2015-01-01", "2018-12-31"),
        "2019_2022": ("2019-01-01", "2022-12-30"),
        "2022_plus": ("2022-01-03", end),
        "2023_plus_observed": ("2023-01-03", end),
    }
    period_results: dict[str, object] = {}
    for label, (start, finish) in periods.items():
        row: dict[str, object] = {
            "HWM75": base.period_metrics(base_curve, start, finish),
        }
        for lookback in LOOKBACKS:
            row[str(lookback)] = base.period_metrics(curves[lookback], start, finish)
        period_results[label] = row

    harsh: dict[str, object] = {}
    for lookback in LOOKBACKS:
        _, metrics, _ = run_lookback(
            oneway,
            monthly,
            rs,
            base,
            frames,
            active,
            end,
            0.002,
            0.002,
            lookback,
        )
        harsh[str(lookback)] = metrics

    cagr_values = [float(metrics_by_lookback[x]["cagr_pct"]) for x in LOOKBACKS]
    mdd_values = [float(metrics_by_lookback[x]["mdd_pct"]) for x in LOOKBACKS]
    sharpe_values = [float(metrics_by_lookback[x]["sharpe"]) for x in LOOKBACKS]
    robustness = {
        "cagr_range_pp": round(max(cagr_values) - min(cagr_values), 2),
        "mdd_range_pp": round(max(mdd_values) - min(mdd_values), 2),
        "sharpe_range": round(max(sharpe_values) - min(sharpe_values), 3),
        "all_cagr_above_22": all(value >= 22.0 for value in cagr_values),
        "all_mdd_better_than_minus_32": all(value >= -32.0 for value in mdd_values),
        "all_5y_win_rate_above_95": all(
            float(results[str(x)]["rolling_5y"]["win_rate_pct"]) >= 95.0
            for x in LOOKBACKS
        ),
        "interpretation": (
            "Do not select the best lookback. The frozen 126-session rule is considered "
            "locally robust only if neighboring 105/147-session variants preserve broadly "
            "similar CAGR, drawdown, Sharpe, and rolling behavior."
        ),
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS_ONEWAY_LOOKBACK_ROBUSTNESS_NO_PRODUCTION_CHANGE",
        "lookbacks": list(LOOKBACKS),
        "baseline": base_metrics,
        "results": results,
        "periods": period_results,
        "harsh_fee20_slip20": harsh,
        "robustness": robustness,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("RESULTS", report["results"])
    print("PERIODS", report["periods"])
    print("HARSH", report["harsh_fee20_slip20"])
    print("ROBUSTNESS", report["robustness"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
