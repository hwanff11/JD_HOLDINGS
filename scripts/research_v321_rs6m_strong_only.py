#!/usr/bin/env python3
"""Allow the diversified SOXL sleeve only when V3.2.1 leverage is 1.5x."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
MONTHLY_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_monthly_lock.py"
START = "2011-01-03"


def load_monthly():
    spec = importlib.util.spec_from_file_location("rs_monthly", MONTHLY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load monthly RS module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def strong_only_weights(leverage: float, use_soxx: bool) -> dict[str, float]:
    if leverage <= 1.0:
        return {"QQQ": max(0.0, leverage)}
    sleeve = (leverage - 1.0) / 2.0
    result = {"QQQ": 1.0 - sleeve}
    if use_soxx and leverage >= 1.5:
        result["TQQQ"] = sleeve / 2.0
        result["SOXL"] = sleeve / 2.0
    else:
        result["TQQQ"] = sleeve
    return result


def run_strong_only(monthly, rs, base, frames, active, end, fee, slippage):
    original = monthly.weights
    monthly.weights = strong_only_weights
    try:
        return monthly.simulate(rs, base, frames, active, end, fee, slippage)
    finally:
        monthly.weights = original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    monthly = load_monthly()
    rs = monthly.load_rs()
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
    base_curve, base_metrics, _ = rs.simulate(
        base, base_policy, frames, active, end, fee, slippage
    )
    monthly_curve, monthly_metrics, _ = monthly.simulate(
        rs, base, frames, active, end, fee, slippage
    )
    strong_curve, strong_metrics, strong_state = run_strong_only(
        monthly, rs, base, frames, active, end, fee, slippage
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
            "RS6M_MONTHLY": base.period_metrics(monthly_curve, start, finish),
            "RS6M_STRONG_ONLY": base.period_metrics(strong_curve, start, finish),
        }
        for label, (start, finish) in periods.items()
    }

    _, strong_harsh, _ = run_strong_only(
        monthly, rs, base, frames, active, end, 0.002, 0.002
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS6M_STRONG_ONLY_RESEARCH_NO_PRODUCTION_CHANGE",
        "rule": (
            "Monthly 6m SOXX-vs-QQQ relative strength is frozen. The 50/50 "
            "TQQQ/SOXL leverage-sleeve split is allowed only in the 1.5x "
            "strong regime; 1.25x uses TQQQ only."
        ),
        "full": {
            "QQQ": qqq_metrics,
            "BASE_TQQQ": {
                "metrics": base_metrics,
                "rolling_3y": rs.rolling_compare(base_curve, qqq, 3),
                "rolling_5y": rs.rolling_compare(base_curve, qqq, 5),
            },
            "RS6M_MONTHLY": {
                "metrics": monthly_metrics,
                "rolling_3y": rs.rolling_compare(monthly_curve, qqq, 3),
                "rolling_5y": rs.rolling_compare(monthly_curve, qqq, 5),
            },
            "RS6M_STRONG_ONLY": {
                "metrics": strong_metrics,
                "rolling_3y": rs.rolling_compare(strong_curve, qqq, 3),
                "rolling_5y": rs.rolling_compare(strong_curve, qqq, 5),
                "soxl_sleeve_days_pct_by_year": rs.yearly_soxl_share(strong_state),
            },
        },
        "periods": period_results,
        "harsh_fee20_slip20": {"RS6M_STRONG_ONLY": strong_harsh},
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
