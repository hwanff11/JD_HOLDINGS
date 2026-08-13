#!/usr/bin/env python3
"""Test a diversified 6-month relative-strength leverage sleeve."""

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
START = "2011-01-03"


def load_rs():
    spec = importlib.util.spec_from_file_location("rs_booster", RS_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load relative-strength module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def split_weights(policy, leverage, qqq_row, soxx_row):
    if leverage <= 1.0:
        return {"QQQ": max(0.0, leverage)}
    sleeve = (leverage - 1.0) / 2.0
    weights = {"QQQ": 1.0 - sleeve}
    values = (
        qqq_row.get("ret126"),
        soxx_row.get("ret126"),
    )
    soxx_wins = False
    if not any(pd.isna(value) for value in values):
        q126 = float(qqq_row["ret126"])
        s126 = float(soxx_row["ret126"])
        soxx_wins = s126 > 0 and s126 > q126
    if soxx_wins:
        weights["TQQQ"] = sleeve / 2.0
        weights["SOXL"] = sleeve / 2.0
    else:
        weights["TQQQ"] = sleeve
    return weights


def run_split(rs, base, frames, active, end, fee, slippage):
    original = rs.leverage_weights
    rs.leverage_weights = split_weights
    try:
        policy = rs.BoosterPolicy("RS_6M_SPLIT", "rs6m_split")
        return rs.simulate(base, policy, frames, active, end, fee, slippage)
    finally:
        rs.leverage_weights = original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_rs()
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
    qqq_metrics = base.summarize(qqq, [1.0] * len(qqq), 1)

    base_policy = rs.BoosterPolicy("BASE_TQQQ", "base")
    rs6_policy = rs.BoosterPolicy("RS_6M", "rs6m")
    base_curve, base_metrics, _ = rs.simulate(
        base, base_policy, frames, active, end, fee, slippage
    )
    rs6_curve, rs6_metrics, _ = rs.simulate(
        base, rs6_policy, frames, active, end, fee, slippage
    )
    split_curve, split_metrics, split_state = run_split(
        rs, base, frames, active, end, fee, slippage
    )

    periods = {
        "2011_2018": ("2011-01-03", "2018-12-31"),
        "2019_2022": ("2019-01-01", "2022-12-30"),
        "2022_plus": ("2022-01-03", end),
        "2023_plus_observed": ("2023-01-03", end),
    }
    period_results = {
        label: {
            "QQQ": base.period_metrics(qqq, start, finish),
            "BASE_TQQQ": base.period_metrics(base_curve, start, finish),
            "RS_6M": base.period_metrics(rs6_curve, start, finish),
            "RS_6M_SPLIT": base.period_metrics(split_curve, start, finish),
        }
        for label, (start, finish) in periods.items()
    }

    _, split_harsh, _ = run_split(rs, base, frames, active, end, 0.002, 0.002)
    _, base_harsh, _ = rs.simulate(
        base, base_policy, frames, active, end, 0.002, 0.002
    )
    _, rs6_harsh, _ = rs.simulate(
        base, rs6_policy, frames, active, end, 0.002, 0.002
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS6M_SPLIT_RESEARCH_NO_PRODUCTION_CHANGE",
        "rule": (
            "HWM75 and V3.2.1 core frozen. When leverage>1 and SOXX 6m return "
            "is positive and exceeds QQQ 6m return, split the leveraged sleeve "
            "50/50 between TQQQ and SOXL; otherwise use TQQQ only."
        ),
        "full": {
            "QQQ": qqq_metrics,
            "BASE_TQQQ": {
                "metrics": base_metrics,
                "rolling_3y": rs.rolling_compare(base_curve, qqq, 3),
                "rolling_5y": rs.rolling_compare(base_curve, qqq, 5),
            },
            "RS_6M": {
                "metrics": rs6_metrics,
                "rolling_3y": rs.rolling_compare(rs6_curve, qqq, 3),
                "rolling_5y": rs.rolling_compare(rs6_curve, qqq, 5),
            },
            "RS_6M_SPLIT": {
                "metrics": split_metrics,
                "rolling_3y": rs.rolling_compare(split_curve, qqq, 3),
                "rolling_5y": rs.rolling_compare(split_curve, qqq, 5),
                "soxl_sleeve_days_pct_by_year": rs.yearly_soxl_share(split_state),
            },
        },
        "periods": period_results,
        "harsh_fee20_slip20": {
            "BASE_TQQQ": base_harsh,
            "RS_6M": rs6_harsh,
            "RS_6M_SPLIT": split_harsh,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("FULL", report["full"])
    print("PERIODS", report["periods"])
    print("HARSH", report["harsh_fee20_slip20"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
