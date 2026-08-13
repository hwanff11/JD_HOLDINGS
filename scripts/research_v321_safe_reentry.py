#!/usr/bin/env python3
"""Test safe 1.0x re-entry after the V3.2.1 volatility brake clears."""

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


def make_safe_reentry():
    was_high_vol = False

    def safe_reentry(
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
            current_lev = 1.0
            monthly_target = 1.0
            was_high_vol = False

        return current_lev, monthly_target

    return safe_reentry


def run_candidate(core, base, frames, active, end, fee, slippage):
    original_next = core.next_leverage
    core.next_leverage = make_safe_reentry()
    try:
        policy = core.CorePolicy("SAFE_1X_REENTRY", "safe_1x_reentry")
        return core.simulate(
            base,
            policy,
            frames,
            active,
            end,
            fee,
            slippage,
        )
    finally:
        core.next_leverage = original_next


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
        base,
        baseline_policy,
        frames,
        active,
        end,
        fee,
        slippage,
    )
    candidate, candidate_diag = run_candidate(
        core,
        base,
        frames,
        active,
        end,
        fee,
        slippage,
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
            "BASE_MONTHLY_STICKY": base.period_metrics(
                baseline,
                start,
                finish,
            ),
            "SAFE_1X_REENTRY": base.period_metrics(
                candidate,
                start,
                finish,
            ),
        }
        for label, (start, finish) in periods.items()
    }

    qqq_harsh = base.benchmark(raw["QQQ"], START, end, 0.002, 0.002)
    _, harsh_diag = run_candidate(
        core,
        base,
        frames,
        active,
        end,
        0.002,
        0.002,
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "SAFE_1X_REENTRY_RESEARCH_NO_PRODUCTION_CHANGE",
        "rule": (
            "keep monthly V3.2.1 schedule; vol20>=30% forces 0.5x; "
            "on the first close below 30% after a high-vol episode, restore "
            "exactly 1.0x and wait for the next normal monthly regime reset"
        ),
        "full": {
            "QQQ": qqq_metrics,
            "BASE_MONTHLY_STICKY": {
                "metrics": baseline_diag["metrics"],
                "rolling_3y": core.rolling_compare(baseline, qqq, 3),
                "rolling_5y": core.rolling_compare(baseline, qqq, 5),
            },
            "SAFE_1X_REENTRY": {
                "metrics": candidate_diag["metrics"],
                "rolling_3y": core.rolling_compare(candidate, qqq, 3),
                "rolling_5y": core.rolling_compare(candidate, qqq, 5),
                "2025_months": core.monthly_table(
                    candidate,
                    qqq,
                    candidate_diag["leverage"],
                    2025,
                ),
            },
        },
        "periods": period_results,
        "harsh_fee20_slip20": {
            "QQQ": base.summarize(qqq_harsh, [1.0] * len(qqq_harsh), 1),
            "SAFE_1X_REENTRY": harsh_diag["metrics"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("FULL", report["full"])
    print("PERIODS", report["periods"])
    print("HARSH", report["harsh_fee20_slip20"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
