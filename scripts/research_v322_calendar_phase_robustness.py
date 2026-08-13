#!/usr/bin/env python3
"""Check whether frozen RS6M one-way performance depends on exact month-start timing.

This is a robustness test, not a phase optimization. V3.2.1 base-leverage monthly
resets remain unchanged. Only the semiconductor relative-strength sleeve's monthly
re-entry decision is shifted to the 1st, 6th, or 11th trading session of each month.
The one-way daily exit remains active between entry decision dates.
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
CAPITAL = 50_000.0
START = "2011-01-03"
SYMBOLS = ("QQQ", "TQQQ", "SOXL")
PHASES = (0, 5, 10)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def simulate_phase(
    rs,
    monthly,
    base,
    frames,
    active,
    end,
    fee,
    slippage,
    phase: int,
):
    index = frames["QQQ"].index
    for symbol in ("TQQQ", "SOXL", "SOXX"):
        index = index.intersection(frames[symbol].index)
    sessions = index[(index >= pd.Timestamp(START)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if prior.empty:
        raise ValueError("warmup history is insufficient")

    session_frame = pd.DataFrame(index=sessions)
    session_frame["month"] = session_frame.index.to_period("M")
    session_frame["month_day"] = session_frame.groupby("month").cumcount()

    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = CAPITAL
    high_water = CAPITAL
    equity_values: list[float] = []
    exposures: list[float] = []
    sizing_history: list[float] = []
    soxl_state: list[bool] = []
    fills = 0

    prior_ts = prior[-1]
    base_lev = base.v321_leverage(frames["QQQ"].loc[prior_ts])
    rs_selected = monthly.soxx_wins_6m(
        frames["QQQ"].loc[prior_ts], frames["SOXX"].loc[prior_ts]
    )
    pending_base = monthly.weights(base_lev, rs_selected)
    pending = base.overlay_target(
        pending_base,
        bool(active["TQQQ"].loc[prior_ts]),
        bool(active["SOXL"].loc[prior_ts]),
    )
    current: dict[str, float] | None = None
    last_month = str(prior_ts.to_period("M"))

    for timestamp in sessions:
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
        size = CAPITAL + 0.75 * max(0.0, high_water - CAPITAL)
        size = max(0.0, min(size, open_equity))
        if pending != current:
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
            current = pending

        liquidation = sum(
            holdings[symbol] * closes[symbol] * (1 - fee)
            for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        sizing_history.append(size)
        soxl_state.append(rs_selected and base_lev > 1.0)
        high_water = max(high_water, equity)

        qqq_row = frames["QQQ"].loc[timestamp]
        soxx_row = frames["SOXX"].loc[timestamp]
        month = str(timestamp.to_period("M"))
        month_changed = month != last_month
        if month_changed:
            # Keep the production-research core timing exactly at month start.
            base_lev = base.v321_leverage(qqq_row)
            last_month = month
        elif float(qqq_row["vol20"]) >= 0.30 and base_lev > 0.5:
            base_lev = 0.5

        month_day = int(session_frame.loc[timestamp, "month_day"])
        if month_day == phase:
            # The only shifted element: permit a fresh semiconductor sleeve decision.
            rs_selected = monthly.soxx_wins_6m(qqq_row, soxx_row)
        elif rs_selected and not monthly.soxx_wins_6m(qqq_row, soxx_row):
            # Frozen one-way exit: leave once and wait for the next phase reset.
            rs_selected = False

        pending_base = monthly.weights(base_lev, rs_selected)
        pending = base.overlay_target(
            pending_base,
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
    return equity, metrics, pd.Series(soxl_state, index=sessions)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_module("rs_booster_phase", RS_SCRIPT)
    split = load_module("rs_split_phase", SPLIT_SCRIPT)
    monthly = load_module("rs_monthly_phase", MONTHLY_SCRIPT)
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

    curves: dict[int, pd.Series] = {}
    metrics_by_phase: dict[int, dict] = {}
    results: dict[str, object] = {}
    for phase in PHASES:
        curve, metrics, state = simulate_phase(
            rs,
            monthly,
            base,
            frames,
            active,
            end,
            fee,
            slippage,
            phase,
        )
        curves[phase] = curve
        metrics_by_phase[phase] = metrics
        key = str(phase + 1)
        results[key] = {
            "decision_trading_day": phase + 1,
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
        for phase in PHASES:
            row[str(phase + 1)] = base.period_metrics(curves[phase], start, finish)
        period_results[label] = row

    harsh: dict[str, object] = {}
    for phase in PHASES:
        _, metrics, _ = simulate_phase(
            rs,
            monthly,
            base,
            frames,
            active,
            end,
            0.002,
            0.002,
            phase,
        )
        harsh[str(phase + 1)] = metrics

    cagrs = [float(metrics_by_phase[x]["cagr_pct"]) for x in PHASES]
    mdds = [float(metrics_by_phase[x]["mdd_pct"]) for x in PHASES]
    sharpes = [float(metrics_by_phase[x]["sharpe"]) for x in PHASES]
    robustness = {
        "cagr_range_pp": round(max(cagrs) - min(cagrs), 2),
        "mdd_range_pp": round(max(mdds) - min(mdds), 2),
        "sharpe_range": round(max(sharpes) - min(sharpes), 3),
        "all_cagr_above_22": all(value >= 22.0 for value in cagrs),
        "all_mdd_better_than_minus_32": all(value >= -32.0 for value in mdds),
        "all_5y_win_rate_above_95": all(
            float(results[str(x + 1)]["rolling_5y"]["win_rate_pct"]) >= 95.0
            for x in PHASES
        ),
        "interpretation": (
            "Do not choose the best calendar phase. The frozen first-trading-day RS reset "
            "is considered calendar-robust only if shifting that sleeve decision to the "
            "6th and 11th trading days preserves broadly similar long-run behavior."
        ),
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS_ONEWAY_CALENDAR_PHASE_ROBUSTNESS_NO_PRODUCTION_CHANGE",
        "decision_trading_days": [1, 6, 11],
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
