#!/usr/bin/env python3
"""Test 6M relative-strength entry with a 1M relative-strength one-way exit."""

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
CAPITAL = 50_000.0
START = "2011-01-03"
SYMBOLS = ("QQQ", "TQQQ", "SOXL")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def one_month_soxx_wins(qqq_row: pd.Series, soxx_row: pd.Series) -> bool:
    q21 = qqq_row.get("ret21")
    s21 = soxx_row.get("ret21")
    if pd.isna(q21) or pd.isna(s21):
        return True
    return float(s21) > float(q21)


def simulate(rs, monthly, base, frames, active, end, fee, slippage):
    index = frames["QQQ"].index
    for symbol in ("TQQQ", "SOXL", "SOXX"):
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
    soxl_state: list[bool] = []
    fills = 0

    prior_ts = prior[-1]
    base_lev = base.v321_leverage(frames["QQQ"].loc[prior_ts])
    monthly_soxx = monthly.soxx_wins_6m(
        frames["QQQ"].loc[prior_ts], frames["SOXX"].loc[prior_ts]
    )
    pending_base = monthly.weights(base_lev, monthly_soxx)
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
        soxl_state.append(monthly_soxx and base_lev > 1.0)
        high_water = max(high_water, equity)

        qqq_row = frames["QQQ"].loc[timestamp]
        soxx_row = frames["SOXX"].loc[timestamp]
        month = str(timestamp.to_period("M"))
        if month != last_month:
            base_lev = base.v321_leverage(qqq_row)
            monthly_soxx = monthly.soxx_wins_6m(qqq_row, soxx_row)
            last_month = month
        else:
            if float(qqq_row["vol20"]) >= 0.30 and base_lev > 0.5:
                base_lev = 0.5
            if monthly_soxx and (
                not monthly.soxx_wins_6m(qqq_row, soxx_row)
                or not one_month_soxx_wins(qqq_row, soxx_row)
            ):
                monthly_soxx = False

        pending_base = monthly.weights(base_lev, monthly_soxx)
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

    rs = load_module("rs_booster_1m", RS_SCRIPT)
    split = load_module("rs_split_1m", SPLIT_SCRIPT)
    monthly = load_module("rs_monthly_1m", MONTHLY_SCRIPT)
    oneway = load_module("rs_oneway_1m", ONEWAY_SCRIPT)
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
    qqq_features = base.features(raw["QQQ"], raw["SPY"])
    if "ret21" not in qqq_features:
        qqq_features["ret21"] = qqq_features["close"].pct_change(21)
    soxx_features = rs.momentum_features(raw["SOXX"])
    if "ret21" not in soxx_features:
        soxx_features["ret21"] = soxx_features["close"].pct_change(21)
    frames = {
        "QQQ": qqq_features,
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
        "SOXX": soxx_features,
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = base.build_active(config, raw, end, slippage)
    qqq = base.benchmark(raw["QQQ"], START, end, fee, slippage)

    base_policy = rs.BoosterPolicy("BASE_TQQQ", "base")
    base_curve, base_metrics, _ = rs.simulate(
        base, base_policy, frames, active, end, fee, slippage
    )
    one_curve, one_metrics, _ = oneway.simulate(
        rs, monthly, base, frames, active, end, fee, slippage
    )
    fast_curve, fast_metrics, fast_state = simulate(
        rs, monthly, base, frames, active, end, fee, slippage
    )

    periods = {
        "2011_2014": ("2011-01-03", "2014-12-31"),
        "2015_2018": ("2015-01-01", "2018-12-31"),
        "2019_2022": ("2019-01-01", "2022-12-30"),
        "2022_plus": ("2022-01-03", end),
        "2023_plus_observed": ("2023-01-03", end),
    }
    period_results = {
        label: {
            "HWM75": base.period_metrics(base_curve, start, finish),
            "RS6M_ONEWAY": base.period_metrics(one_curve, start, finish),
            "RS6M_ENTRY_RS1M_EXIT": base.period_metrics(fast_curve, start, finish),
        }
        for label, (start, finish) in periods.items()
    }

    _, harsh_metrics, _ = simulate(
        rs, monthly, base, frames, active, end, 0.002, 0.002
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS6M_ENTRY_RS1M_EXIT_RESEARCH_NO_PRODUCTION_CHANGE",
        "rule": (
            "monthly entry remains SOXX 126-session return > 0 and > QQQ 126-session return. "
            "During the month, exit the SOXL half-sleeve to TQQQ once if either the 6M "
            "condition fails or SOXX 21-session return is no longer above QQQ 21-session "
            "return. No SOXL re-entry until the next monthly reset."
        ),
        "full": {
            "HWM75": {
                "metrics": base_metrics,
                "rolling_3y": split.rolling_distribution(base_curve, qqq, 3),
                "rolling_5y": split.rolling_distribution(base_curve, qqq, 5),
            },
            "RS6M_ONEWAY": {
                "metrics": one_metrics,
                "rolling_3y": split.rolling_distribution(one_curve, qqq, 3),
                "rolling_5y": split.rolling_distribution(one_curve, qqq, 5),
            },
            "RS6M_ENTRY_RS1M_EXIT": {
                "metrics": fast_metrics,
                "rolling_1y": split.rolling_distribution(fast_curve, qqq, 1),
                "rolling_3y": split.rolling_distribution(fast_curve, qqq, 3),
                "rolling_5y": split.rolling_distribution(fast_curve, qqq, 5),
                "soxl_sleeve_days_pct_by_year": rs.yearly_soxl_share(fast_state),
            },
        },
        "periods": period_results,
        "harsh_fee20_slip20": {
            "RS6M_ENTRY_RS1M_EXIT": harsh_metrics,
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
