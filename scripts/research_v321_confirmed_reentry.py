#!/usr/bin/env python3
"""Compare simple trend-confirmed 1.0x re-entry rules after the volatility brake."""

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
CORE_SCRIPT = ROOT / "scripts" / "research_v321_core_timing.py"
START = "2011-01-03"


def load_core():
    spec = importlib.util.spec_from_file_location("core_timing", CORE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load core timing module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def confirmed(row, mode: str) -> bool:
    if mode == "sma50":
        return float(row["close"]) > float(row["sma50"])
    if mode == "ret21":
        return float(row["ret21"]) > 0.0
    if mode == "either":
        return (
            float(row["close"]) > float(row["sma50"])
            or float(row["ret21"]) > 0.0
        )
    raise ValueError(mode)


def make_confirmed_reentry(mode: str):
    was_high_vol = False

    def confirmed_reentry(
        base,
        policy,
        row,
        month_changed,
        refresh_5d,
        monthly_target,
        current_lev,
    ):
        del policy, refresh_5d
        nonlocal was_high_vol

        if month_changed:
            current_lev = base.v321_leverage(row)
            monthly_target = current_lev

        high_vol = float(row["vol20"]) >= 0.30
        if high_vol:
            was_high_vol = True
            return 0.5, monthly_target

        if was_high_vol:
            if confirmed(row, mode):
                current_lev = 1.0
                monthly_target = 1.0
                was_high_vol = False
            else:
                return 0.5, monthly_target

        return current_lev, monthly_target

    return confirmed_reentry


def run_candidate(core, base, frames, active, end, fee, slippage, mode: str):
    original_next = core.next_leverage
    core.next_leverage = make_confirmed_reentry(mode)
    try:
        policy = core.CorePolicy(f"CONFIRMED_1X_{mode.upper()}", f"confirmed_{mode}")
        return core.simulate(base, policy, frames, active, end, fee, slippage)
    finally:
        core.next_leverage = original_next


def compact(base, core, equity, diag, qqq):
    return {
        "metrics": diag["metrics"],
        "rolling_3y": core.rolling_compare(equity, qqq, 3),
        "rolling_5y": core.rolling_compare(equity, qqq, 5),
        "2025_months": core.monthly_table(equity, qqq, diag["leverage"], 2025),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    core = load_core()
    base = core.load_base()
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
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = base.build_active(config, raw, end, slippage)
    qqq = base.benchmark(raw["QQQ"], START, end, fee, slippage)
    qqq_metrics = base.summarize(qqq, [1.0] * len(qqq), 1)

    baseline_policy = core.CorePolicy("BASE_MONTHLY_STICKY", "monthly_sticky")
    baseline, baseline_diag = core.simulate(
        base, baseline_policy, frames, active, end, fee, slippage
    )

    candidates = {}
    candidate_series = {}
    candidate_diags = {}
    for mode in ("sma50", "ret21", "either"):
        equity, diag = run_candidate(
            core, base, frames, active, end, fee, slippage, mode
        )
        name = f"CONFIRMED_1X_{mode.upper()}"
        candidate_series[name] = equity
        candidate_diags[name] = diag
        candidates[name] = compact(base, core, equity, diag, qqq)

    periods = {
        "2011_2018": ("2011-01-03", "2018-12-31"),
        "2019_2022": ("2019-01-01", "2022-12-30"),
        "2022_plus": ("2022-01-03", end),
        "2023_plus_observed": ("2023-01-03", end),
    }
    period_results = {}
    for label, (start, finish) in periods.items():
        row = {
            "QQQ": base.period_metrics(qqq, start, finish),
            "BASE_MONTHLY_STICKY": base.period_metrics(baseline, start, finish),
        }
        for name, equity in candidate_series.items():
            row[name] = base.period_metrics(equity, start, finish)
        period_results[label] = row

    harsh = {}
    for mode in ("sma50", "ret21", "either"):
        _, diag = run_candidate(core, base, frames, active, end, 0.002, 0.002, mode)
        harsh[f"CONFIRMED_1X_{mode.upper()}"] = diag["metrics"]

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "CONFIRMED_REENTRY_RESEARCH_NO_PRODUCTION_CHANGE",
        "selection_rule": (
            "compare only three simple confirmations after vol20 falls below 30%: "
            "QQQ close>SMA50, ret21>0, or either; re-enter at exactly 1.0x and "
            "wait for the next monthly reset"
        ),
        "full": {
            "QQQ": qqq_metrics,
            "BASE_MONTHLY_STICKY": {
                "metrics": baseline_diag["metrics"],
                "rolling_3y": core.rolling_compare(baseline, qqq, 3),
                "rolling_5y": core.rolling_compare(baseline, qqq, 5),
            },
            **candidates,
        },
        "periods": period_results,
        "harsh_fee20_slip20": harsh,
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
