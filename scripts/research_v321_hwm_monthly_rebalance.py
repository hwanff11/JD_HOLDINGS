#!/usr/bin/env python3
"""Test whether HWM75 should reconcile sizing every monthly regime evaluation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = ROOT / "scripts" / "research_v321_controlled_compounding.py"
START = "2011-01-03"
CAPITAL = 50_000.0
SYMBOLS = ("QQQ", "TQQQ", "SOXL")


def load_base():
    spec = importlib.util.spec_from_file_location("controlled_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load controlled compounding helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def simulate(base, frames, active, end, fee, slippage, *, monthly_reconcile: bool):
    index = frames["QQQ"].index
    for symbol in SYMBOLS[1:]:
        index = index.intersection(frames[symbol].index)
    sessions = index[(index >= pd.Timestamp(START)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if prior.empty:
        raise ValueError("warmup history is insufficient")

    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = CAPITAL
    high_water = CAPITAL
    equity_values: list[float] = []
    exposures: list[float] = []
    sizing_history: list[float] = []
    fills = 0
    forced_reconciles = 0

    prior_ts = prior[-1]
    leverage = base.v321_leverage(frames["QQQ"].loc[prior_ts])
    pending = base.overlay_target(
        base.leverage_weights(leverage),
        bool(active["TQQQ"].loc[prior_ts]),
        bool(active["SOXL"].loc[prior_ts]),
    )
    current: dict[str, float] | None = None
    last_month = str(prior_ts.to_period("M"))
    force_next = False

    for timestamp in sessions:
        opens = {symbol: float(frames[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS}
        closes = {symbol: float(frames[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS}
        open_equity = cash + sum(
            holdings[symbol] * opens[symbol] * (1 - fee) for symbol in SYMBOLS
        )
        sizing_base = CAPITAL + 0.75 * max(0.0, high_water - CAPITAL)
        sizing_base = max(0.0, min(sizing_base, open_equity))

        target_changed = pending != current
        should_rebalance = target_changed or force_next
        if should_rebalance:
            cash, count = base.rebalance_weights(
                pending,
                holdings,
                opens,
                cash,
                0.0,
                fee,
                slippage,
                sizing_base,
            )
            fills += count
            if force_next and not target_changed:
                forced_reconciles += 1
            current = pending
            force_next = False

        liquidation = sum(
            holdings[symbol] * closes[symbol] * (1 - fee) for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        sizing_history.append(sizing_base)
        high_water = max(high_water, equity)

        row = frames["QQQ"].loc[timestamp]
        month = str(timestamp.to_period("M"))
        month_changed = month != last_month
        if month_changed:
            leverage = base.v321_leverage(row)
            last_month = month
            if monthly_reconcile:
                force_next = True
        elif float(row["vol20"]) >= 0.30 and leverage > 0.5:
            leverage = 0.5

        pending = base.overlay_target(
            base.leverage_weights(leverage),
            bool(active["TQQQ"].loc[timestamp]),
            bool(active["SOXL"].loc[timestamp]),
        )

    equity = pd.Series(equity_values, index=sessions)
    metrics = base.summarize(
        equity,
        exposures,
        fills,
        sizing_history,
        [0.0] * len(sizing_history),
    )
    metrics["forced_monthly_reconciles"] = forced_reconciles
    return equity, metrics


def rolling(candidate: pd.Series, benchmark: pd.Series, years: int) -> dict[str, float]:
    common = candidate.index.intersection(benchmark.index)
    excess: list[float] = []
    for start in common[::21]:
        target = start + pd.DateOffset(years=years)
        ends = common[common >= target]
        if ends.empty:
            break
        end = ends[0]
        span = max((end - start).days / 365.2425, 1 / 365.2425)
        c = (float(candidate.loc[end] / candidate.loc[start]) ** (1 / span) - 1) * 100
        b = (float(benchmark.loc[end] / benchmark.loc[start]) ** (1 / span) - 1) * 100
        excess.append(c - b)
    values = np.asarray(excess)
    return {
        "win_rate_pct": round(float(np.mean(values > 0)) * 100, 2),
        "median_excess_pp": round(float(np.median(values)), 2),
        "worst_excess_pp": round(float(np.min(values)), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = load_base()
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

    baseline, baseline_metrics = simulate(
        base, frames, active, end, fee, slippage, monthly_reconcile=False
    )
    monthly, monthly_metrics = simulate(
        base, frames, active, end, fee, slippage, monthly_reconcile=True
    )

    periods = {
        "2011_2018": ("2011-01-03", "2018-12-31"),
        "2019_2022": ("2019-01-02", "2022-12-30"),
        "2022_plus": ("2022-01-03", end),
        "2023_plus_observed": ("2023-01-03", end),
    }
    period_results = {
        label: {
            "QQQ": base.period_metrics(qqq, start, finish),
            "HWM75_EVENT": base.period_metrics(baseline, start, finish),
            "HWM75_MONTHLY_RECONCILE": base.period_metrics(monthly, start, finish),
        }
        for label, (start, finish) in periods.items()
    }

    costs: dict[str, object] = {}
    for label, scenario_fee, scenario_slippage in (
        ("base", fee, slippage),
        ("slip30", fee, 0.003),
        ("fee20_slip20", 0.002, 0.002),
    ):
        _, event_metrics = simulate(
            base,
            frames,
            active,
            end,
            scenario_fee,
            scenario_slippage,
            monthly_reconcile=False,
        )
        _, reconcile_metrics = simulate(
            base,
            frames,
            active,
            end,
            scenario_fee,
            scenario_slippage,
            monthly_reconcile=True,
        )
        costs[label] = {
            "HWM75_EVENT": event_metrics,
            "HWM75_MONTHLY_RECONCILE": reconcile_metrics,
        }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "HWM75_MONTHLY_RECONCILIATION_RESEARCH_NO_PRODUCTION_CHANGE",
        "rule": (
            "At the existing monthly V3.2.1 regime evaluation, reconcile holdings "
            "to the current HWM75 risk budget even when target leverage is unchanged."
        ),
        "full": {
            "QQQ": qqq_metrics,
            "HWM75_EVENT": {
                "metrics": baseline_metrics,
                "rolling_3y": rolling(baseline, qqq, 3),
                "rolling_5y": rolling(baseline, qqq, 5),
            },
            "HWM75_MONTHLY_RECONCILE": {
                "metrics": monthly_metrics,
                "rolling_3y": rolling(monthly, qqq, 3),
                "rolling_5y": rolling(monthly, qqq, 5),
            },
        },
        "periods": period_results,
        "cost_stress": costs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FULL", report["full"])
    print("PERIODS", period_results)
    print("COST", costs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
