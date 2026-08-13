#!/usr/bin/env python3
"""Test simple V3.2.1 core timing alternatives with HWM75 fixed."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = ROOT / "scripts" / "research_v321_controlled_compounding.py"
CAPITAL = 50_000.0
START = "2011-01-03"
SYMBOLS = ("QQQ", "TQQQ", "SOXL")


@dataclass(frozen=True)
class CorePolicy:
    name: str
    mode: str


def load_base():
    spec = importlib.util.spec_from_file_location("controlled_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load controlled compounding base module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def brake_active(row: pd.Series, mode: str) -> bool:
    vol = float(row["vol20"])
    if mode == "directional":
        return vol >= 0.40 or (vol >= 0.30 and float(row["ret21"]) < 0)
    if mode == "sma50":
        return vol >= 0.40 or (
            vol >= 0.30 and float(row["close"]) < float(row["sma50"])
        )
    return vol >= 0.30


def next_leverage(
    base,
    policy: CorePolicy,
    row: pd.Series,
    month_changed: bool,
    refresh_5d: bool,
    monthly_target: float,
    current_lev: float,
) -> tuple[float, float]:
    if policy.mode == "monthly_sticky":
        if month_changed:
            current_lev = base.v321_leverage(row)
            monthly_target = current_lev
        elif float(row["vol20"]) >= 0.30 and current_lev > 0.5:
            current_lev = 0.5
        return current_lev, monthly_target

    if policy.mode == "monthly_release":
        if month_changed:
            monthly_target = base.v321_leverage(row)
        current_lev = 0.5 if float(row["vol20"]) >= 0.30 else monthly_target
        return current_lev, monthly_target

    if policy.mode in {"directional", "sma50"}:
        if month_changed:
            monthly_target = base.v321_leverage(row)
        current_lev = 0.5 if brake_active(row, policy.mode) else monthly_target
        return current_lev, monthly_target

    if policy.mode == "weekly_refresh":
        if month_changed or refresh_5d:
            current_lev = base.v321_leverage(row)
            monthly_target = current_lev
        elif float(row["vol20"]) >= 0.30 and current_lev > 0.5:
            current_lev = 0.5
        return current_lev, monthly_target

    if policy.mode == "daily_refresh":
        current_lev = base.v321_leverage(row)
        return current_lev, current_lev

    raise ValueError(policy.mode)


def hwm75_size(open_equity: float, high_water: float) -> float:
    target = CAPITAL + 0.75 * max(0.0, high_water - CAPITAL)
    return max(0.0, min(target, open_equity))


def simulate(base, policy, frames, active, end, fee, slippage):
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
    leverage_history: list[float] = []
    overlay_history: list[bool] = []
    fills = 0

    prior_ts = prior[-1]
    current_lev = base.v321_leverage(frames["QQQ"].loc[prior_ts])
    monthly_target = current_lev
    pending = base.overlay_target(
        base.leverage_weights(current_lev),
        bool(active["TQQQ"].loc[prior_ts]),
        bool(active["SOXL"].loc[prior_ts]),
    )
    pending_lev = current_lev
    pending_overlay = bool(active["TQQQ"].loc[prior_ts]) or bool(
        active["SOXL"].loc[prior_ts]
    )
    current_weights: dict[str, float] | None = None
    last_month = str(prior_ts.to_period("M"))

    for session_number, timestamp in enumerate(sessions, start=1):
        opens = {
            symbol: float(frames[symbol].loc[timestamp, "open"])
            for symbol in SYMBOLS
        }
        closes = {
            symbol: float(frames[symbol].loc[timestamp, "close"])
            for symbol in SYMBOLS
        }
        open_equity = cash + sum(
            holdings[symbol] * opens[symbol] * (1 - fee)
            for symbol in SYMBOLS
        )
        size = hwm75_size(open_equity, high_water)
        if pending != current_weights:
            cash, count = base.rebalance_weights(
                pending,
                holdings,
                opens,
                cash,
                0.0,
                fee,
                slippage,
                size,
            )
            fills += count
            current_weights = pending

        liquidation = sum(
            holdings[symbol] * closes[symbol] * (1 - fee)
            for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        sizing_history.append(size)
        leverage_history.append(pending_lev)
        overlay_history.append(pending_overlay)
        high_water = max(high_water, equity)

        row = frames["QQQ"].loc[timestamp]
        month = str(timestamp.to_period("M"))
        month_changed = month != last_month
        refresh_5d = session_number % 5 == 0
        current_lev, monthly_target = next_leverage(
            base,
            policy,
            row,
            month_changed,
            refresh_5d,
            monthly_target,
            current_lev,
        )
        if month_changed:
            last_month = month

        use_tqqq = bool(active["TQQQ"].loc[timestamp])
        use_soxl = bool(active["SOXL"].loc[timestamp])
        pending = base.overlay_target(
            base.leverage_weights(current_lev),
            use_tqqq,
            use_soxl,
        )
        pending_lev = current_lev
        pending_overlay = use_tqqq or use_soxl

    equity = pd.Series(equity_values, index=sessions)
    metrics = base.summarize(
        equity,
        exposures,
        fills,
        sizing_history,
        [0.0] * len(sizing_history),
    )
    return equity, {
        "metrics": metrics,
        "leverage": pd.Series(leverage_history, index=sessions),
        "overlay": pd.Series(overlay_history, index=sessions),
        "exposure": pd.Series(exposures, index=sessions),
    }


def rolling_compare(candidate: pd.Series, benchmark: pd.Series, years: int):
    common = candidate.index.intersection(benchmark.index)
    rows: list[float] = []
    for start in common[::21]:
        target = start + pd.DateOffset(years=years)
        ends = common[common >= target]
        if ends.empty:
            break
        end = ends[0]
        span = max((end - start).days / 365.2425, 1 / 365.2425)
        cagr_c = (float(candidate.loc[end] / candidate.loc[start]) ** (1 / span) - 1) * 100
        cagr_b = (float(benchmark.loc[end] / benchmark.loc[start]) ** (1 / span) - 1) * 100
        rows.append(cagr_c - cagr_b)
    values = np.asarray(rows)
    return {
        "win_rate_pct": round(float(np.mean(values > 0)) * 100, 2),
        "median_excess_pp": round(float(np.median(values)), 2),
        "worst_excess_pp": round(float(np.min(values)), 2),
    }


def monthly_table(equity: pd.Series, benchmark: pd.Series, leverage: pd.Series, year: int):
    common = equity.index.intersection(benchmark.index)
    e = equity.loc[common]
    b = benchmark.loc[common]
    e_month = e.groupby(e.index.to_period("M")).last()
    b_month = b.groupby(b.index.to_period("M")).last()
    rows: dict[str, object] = {}
    prior_e = float(e.loc[e.index.year < year].iloc[-1])
    prior_b = float(b.loc[b.index.year < year].iloc[-1])
    for period in e_month.index[e_month.index.year == year]:
        current_e = float(e_month.loc[period])
        current_b = float(b_month.loc[period])
        mask = leverage.index.to_period("M") == period
        counts = leverage.loc[mask].value_counts().sort_index()
        rows[str(period)] = {
            "strategy_pct": round((current_e / prior_e - 1) * 100, 2),
            "qqq_pct": round((current_b / prior_b - 1) * 100, 2),
            "excess_pp": round(
                (current_e / prior_e - current_b / prior_b) * 100,
                2,
            ),
            "leverage_days": {
                f"{float(key):.2f}x": int(value)
                for key, value in counts.items()
            },
        }
        prior_e = current_e
        prior_b = current_b
    return rows


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

    policies = (
        CorePolicy("BASE_MONTHLY_STICKY", "monthly_sticky"),
        CorePolicy("MONTHLY_RELEASE", "monthly_release"),
        CorePolicy("DIRECTIONAL_BRAKE", "directional"),
        CorePolicy("SMA50_BRAKE", "sma50"),
        CorePolicy("WEEKLY_REFRESH", "weekly_refresh"),
        CorePolicy("DAILY_REFRESH", "daily_refresh"),
    )
    curves: dict[str, pd.Series] = {}
    diagnostics: dict[str, dict[str, object]] = {}
    results: dict[str, object] = {"QQQ": qqq_metrics}
    for policy in policies:
        equity, diag = simulate(
            base,
            policy,
            frames,
            active,
            end,
            fee,
            slippage,
        )
        curves[policy.name] = equity
        diagnostics[policy.name] = diag
        results[policy.name] = {
            "metrics": diag["metrics"],
            "rolling_3y_vs_qqq": rolling_compare(equity, qqq, 3),
            "rolling_5y_vs_qqq": rolling_compare(equity, qqq, 5),
            "2025_months": monthly_table(
                equity,
                qqq,
                diag["leverage"],
                2025,
            ),
        }

    periods = {
        "2011_2018": ("2011-01-03", "2018-12-31"),
        "2019_2022": ("2019-01-01", "2022-12-30"),
        "2022_plus": ("2022-01-03", end),
        "2023_plus_observed": ("2023-01-03", end),
    }
    period_results = {
        label: {
            "QQQ": base.period_metrics(qqq, start, finish),
            **{
                policy.name: base.period_metrics(
                    curves[policy.name],
                    start,
                    finish,
                )
                for policy in policies
            },
        }
        for label, (start, finish) in periods.items()
    }

    cost_results: dict[str, object] = {}
    for label, scenario_fee, scenario_slippage in (
        ("base", fee, slippage),
        ("harsh_fee20_slip20", 0.002, 0.002),
    ):
        rows: dict[str, object] = {}
        for policy in policies:
            _, diag = simulate(
                base,
                policy,
                frames,
                active,
                end,
                scenario_fee,
                scenario_slippage,
            )
            rows[policy.name] = diag["metrics"]
        cost_results[label] = rows

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "V321_CORE_TIMING_RESEARCH_NO_PRODUCTION_CHANGE",
        "frozen": {
            "hwm_fraction": 0.75,
            "jdss_overlay": 0.05,
            "leverage_levels": [0.5, 1.0, 1.25, 1.5],
            "2023_plus_pristine_oos": False,
        },
        "full": results,
        "periods": period_results,
        "cost_stress": cost_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("FULL", results)
    print("PERIODS", period_results)
    print("COST", cost_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
