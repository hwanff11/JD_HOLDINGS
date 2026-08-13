#!/usr/bin/env python3
"""Test monthly core timing with weekly upward-only recovery."""

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


def recovery_next_leverage(
    base,
    policy,
    row,
    month_changed,
    refresh_5d,
    monthly_target,
    current_lev,
):
    if month_changed:
        current_lev = base.v321_leverage(row)
        monthly_target = current_lev
        return current_lev, monthly_target

    if float(row["vol20"]) >= 0.30:
        return 0.5, monthly_target

    if refresh_5d:
        candidate = base.v321_leverage(row)
        if candidate > current_lev:
            current_lev = candidate
            monthly_target = max(monthly_target, candidate)
    return current_lev, monthly_target


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

    base_policy = core.CorePolicy("BASE_MONTHLY_STICKY", "monthly_sticky")
    baseline, baseline_diag = core.simulate(
        base,
        base_policy,
        frames,
        active,
        end,
        fee,
        slippage,
    )

    original_next = core.next_leverage
    core.next_leverage = recovery_next_leverage
    recovery_policy = core.CorePolicy("WEEKLY_UPSHIFT_ONLY", "weekly_upshift")
    recovery, recovery_diag = core.simulate(
        base,
        recovery_policy,
        frames,
        active,
        end,
        fee,
        slippage,
    )
    core.next_leverage = original_next

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
            "WEEKLY_UPSHIFT_ONLY": base.period_metrics(
                recovery,
                start,
                finish,
            ),
        }
        for label, (start, finish) in periods.items()
    }

    core.next_leverage = recovery_next_leverage
    harsh, harsh_diag = core.simulate(
        base,
        recovery_policy,
        frames,
        active,
        end,
        0.002,
        0.002,
    )
    core.next_leverage = original_next
    qqq_harsh = base.benchmark(raw["QQQ"], START, end, 0.002, 0.002)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RECOVERY_ONLY_RESEARCH_NO_PRODUCTION_CHANGE",
        "rule": (
            "monthly baseline; immediate vol>=30% brake to 0.5x; "
            "every 5 sessions only allow leverage increases from fresh V3.2.1 signal"
        ),
        "full": {
            "QQQ": qqq_metrics,
            "BASE_MONTHLY_STICKY": {
                "metrics": baseline_diag["metrics"],
                "rolling_3y": core.rolling_compare(baseline, qqq, 3),
                "rolling_5y": core.rolling_compare(baseline, qqq, 5),
            },
            "WEEKLY_UPSHIFT_ONLY": {
                "metrics": recovery_diag["metrics"],
                "rolling_3y": core.rolling_compare(recovery, qqq, 3),
                "rolling_5y": core.rolling_compare(recovery, qqq, 5),
                "2025_months": core.monthly_table(
                    recovery,
                    qqq,
                    recovery_diag["leverage"],
                    2025,
                ),
            },
        },
        "periods": period_results,
        "harsh_fee20_slip20": {
            "QQQ": base.summarize(qqq_harsh, [1.0] * len(qqq_harsh), 1),
            "WEEKLY_UPSHIFT_ONLY": harsh_diag["metrics"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("FULL", report["full"])
    print("PERIODS", period_results)
    print("HARSH", report["harsh_fee20_slip20"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
