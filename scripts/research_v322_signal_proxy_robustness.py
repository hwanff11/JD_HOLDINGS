#!/usr/bin/env python3
"""Check whether the frozen RS6M one-way structure depends on SOXX specifically.

The traded assets and all strategy rules remain unchanged. Only the semiconductor
relative-strength reference series is switched between SOXX and SMH.
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
PROXIES = ("SOXX", "SMH")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_proxy(oneway, monthly, rs, base, common_frames, proxy_frame, active, end, fee, slippage):
    frames = dict(common_frames)
    # Historical simulator reads the semiconductor signal from frames["SOXX"].
    # Supplying SMH features here changes only the signal proxy, not the traded SOXL asset.
    frames["SOXX"] = proxy_frame
    return oneway.simulate(rs, monthly, base, frames, active, end, fee, slippage)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_module("rs_booster_proxy", RS_SCRIPT)
    split = load_module("rs_split_proxy", SPLIT_SCRIPT)
    monthly = load_module("rs_monthly_proxy", MONTHLY_SCRIPT)
    oneway = load_module("rs_oneway_proxy", ONEWAY_SCRIPT)
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
    common_frames = {
        "QQQ": base.features(raw["QQQ"], raw["SPY"]),
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
    }
    proxy_frames = {symbol: rs.momentum_features(raw[symbol]) for symbol in PROXIES}
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = base.build_active(config, raw, end, slippage)
    qqq = base.benchmark(raw["QQQ"], START, end, fee, slippage)

    base_frames = dict(common_frames)
    base_frames["SOXX"] = proxy_frames["SOXX"]
    base_policy = rs.BoosterPolicy("BASE_TQQQ", "base")
    base_curve, base_metrics, _ = rs.simulate(
        base, base_policy, base_frames, active, end, fee, slippage
    )

    curves: dict[str, pd.Series] = {}
    results: dict[str, object] = {}
    metrics_by_proxy: dict[str, dict] = {}
    for proxy in PROXIES:
        curve, metrics, state = run_proxy(
            oneway,
            monthly,
            rs,
            base,
            common_frames,
            proxy_frames[proxy],
            active,
            end,
            fee,
            slippage,
        )
        curves[proxy] = curve
        metrics_by_proxy[proxy] = metrics
        results[proxy] = {
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
        for proxy in PROXIES:
            row[proxy] = base.period_metrics(curves[proxy], start, finish)
        period_results[label] = row

    harsh: dict[str, object] = {}
    for proxy in PROXIES:
        _, metrics, _ = run_proxy(
            oneway,
            monthly,
            rs,
            base,
            common_frames,
            proxy_frames[proxy],
            active,
            end,
            0.002,
            0.002,
        )
        harsh[proxy] = metrics

    cagr_values = [float(metrics_by_proxy[x]["cagr_pct"]) for x in PROXIES]
    mdd_values = [float(metrics_by_proxy[x]["mdd_pct"]) for x in PROXIES]
    sharpe_values = [float(metrics_by_proxy[x]["sharpe"]) for x in PROXIES]
    robustness = {
        "cagr_difference_pp": round(abs(cagr_values[0] - cagr_values[1]), 2),
        "mdd_difference_pp": round(abs(mdd_values[0] - mdd_values[1]), 2),
        "sharpe_difference": round(abs(sharpe_values[0] - sharpe_values[1]), 3),
        "both_cagr_above_22": all(value >= 22.0 for value in cagr_values),
        "both_mdd_better_than_minus_32": all(value >= -32.0 for value in mdd_values),
        "both_5y_win_rate_above_95": all(
            float(results[x]["rolling_5y"]["win_rate_pct"]) >= 95.0
            for x in PROXIES
        ),
        "interpretation": (
            "Do not replace SOXX because one proxy backtests better. This test only asks "
            "whether the same semiconductor-vs-QQQ relative-strength structure survives "
            "when measured with another broad semiconductor ETF."
        ),
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS_ONEWAY_SIGNAL_PROXY_ROBUSTNESS_NO_PRODUCTION_CHANGE",
        "signal_proxies": list(PROXIES),
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
