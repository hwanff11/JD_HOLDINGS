#!/usr/bin/env python3
"""Robustness check for the frozen RS6M one-way SOXL sleeve share.

This is not an optimization search. The frozen 50% SOXL share inside the leveraged
sleeve is perturbed to 25% and 75% while all other logic remains unchanged.
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
SOXL_SHARES = (0.25, 0.50, 0.75)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_weights(soxl_share: float):
    def weights(leverage: float, use_soxx: bool) -> dict[str, float]:
        if leverage <= 1.0:
            return {"QQQ": max(0.0, leverage)}
        sleeve = (leverage - 1.0) / 2.0
        result = {"QQQ": 1.0 - sleeve}
        if use_soxx:
            result["TQQQ"] = sleeve * (1.0 - soxl_share)
            result["SOXL"] = sleeve * soxl_share
        else:
            result["TQQQ"] = sleeve
        return result

    return weights


def run_share(oneway, monthly, rs, base, frames, active, end, fee, slippage, share):
    original_weights = monthly.weights
    monthly.weights = make_weights(share)
    try:
        return oneway.simulate(rs, monthly, base, frames, active, end, fee, slippage)
    finally:
        monthly.weights = original_weights


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_module("rs_booster_sleeve", RS_SCRIPT)
    split = load_module("rs_split_sleeve", SPLIT_SCRIPT)
    monthly = load_module("rs_monthly_sleeve", MONTHLY_SCRIPT)
    oneway = load_module("rs_oneway_sleeve", ONEWAY_SCRIPT)
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
    frames = {
        "QQQ": base.features(raw["QQQ"], raw["SPY"]),
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
        "SOXX": rs.momentum_features(raw["SOXX"]),
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
    curves: dict[float, pd.Series] = {}
    metrics_by_share: dict[float, dict] = {}
    for share in SOXL_SHARES:
        curve, metrics, state = run_share(
            oneway,
            monthly,
            rs,
            base,
            frames,
            active,
            end,
            fee,
            slippage,
            share,
        )
        curves[share] = curve
        metrics_by_share[share] = metrics
        key = f"{int(share * 100)}"
        results[key] = {
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
        for share in SOXL_SHARES:
            row[f"{int(share * 100)}"] = base.period_metrics(
                curves[share], start, finish
            )
        period_results[label] = row

    harsh: dict[str, object] = {}
    for share in SOXL_SHARES:
        _, metrics, _ = run_share(
            oneway,
            monthly,
            rs,
            base,
            frames,
            active,
            end,
            0.002,
            0.002,
            share,
        )
        harsh[f"{int(share * 100)}"] = metrics

    cagrs = [float(metrics_by_share[x]["cagr_pct"]) for x in SOXL_SHARES]
    mdds = [float(metrics_by_share[x]["mdd_pct"]) for x in SOXL_SHARES]
    sharpes = [float(metrics_by_share[x]["sharpe"]) for x in SOXL_SHARES]
    robustness = {
        "cagr_range_pp": round(max(cagrs) - min(cagrs), 2),
        "mdd_range_pp": round(max(mdds) - min(mdds), 2),
        "sharpe_range": round(max(sharpes) - min(sharpes), 3),
        "all_cagr_above_22": all(value >= 22.0 for value in cagrs),
        "all_mdd_better_than_minus_32": all(value >= -32.0 for value in mdds),
        "all_5y_win_rate_above_95": all(
            float(results[f"{int(x * 100)}"]["rolling_5y"]["win_rate_pct"])
            >= 95.0
            for x in SOXL_SHARES
        ),
        "interpretation": (
            "Do not select the best sleeve share. The frozen 50% share is considered "
            "structurally robust only if 25% and 75% preserve broadly similar return, "
            "drawdown, Sharpe, and rolling behavior."
        ),
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS_ONEWAY_SLEEVE_ROBUSTNESS_NO_PRODUCTION_CHANGE",
        "soxl_shares_pct": [25, 50, 75],
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
