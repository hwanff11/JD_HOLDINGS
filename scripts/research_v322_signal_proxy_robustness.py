#!/usr/bin/env python3
"""Check whether RS6M one-way behavior survives SOXX -> SMH signal proxy swap."""

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
MONTHLY_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_monthly_lock.py"
SPLIT_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_split.py"
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


def simulate(base, monthly, frames, active, end, fee, slippage, signal_key: str):
    index = frames["QQQ"].index
    for symbol in ("TQQQ", "SOXL", signal_key):
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

    prior_ts = prior[-1]
    base_lev = base.v321_leverage(frames["QQQ"].loc[prior_ts])
    signal_on = monthly.soxx_wins_6m(
        frames["QQQ"].loc[prior_ts], frames[signal_key].loc[prior_ts]
    )
    pending_base = monthly.weights(base_lev, signal_on)
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
            holdings[symbol] * opens[symbol] * (1 - fee) for symbol in SYMBOLS
        )
        size = CAPITAL + 0.75 * max(0.0, high_water - CAPITAL)
        size = max(0.0, min(size, open_equity))
        if pending != current:
            cash, count = base.rebalance_weights(
                pending, holdings, opens, cash, 0.0, fee, slippage, size
            )
            fills += count
            current = pending

        liquidation = sum(
            holdings[symbol] * closes[symbol] * (1 - fee) for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        sizing_history.append(size)
        high_water = max(high_water, equity)

        qqq_row = frames["QQQ"].loc[timestamp]
        signal_row = frames[signal_key].loc[timestamp]
        month = str(timestamp.to_period("M"))
        if month != last_month:
            base_lev = base.v321_leverage(qqq_row)
            signal_on = monthly.soxx_wins_6m(qqq_row, signal_row)
            last_month = month
        else:
            if float(qqq_row["vol20"]) >= 0.30 and base_lev > 0.5:
                base_lev = 0.5
            if signal_on and not monthly.soxx_wins_6m(qqq_row, signal_row):
                signal_on = False

        pending_base = monthly.weights(base_lev, signal_on)
        pending = base.overlay_target(
            pending_base,
            bool(active["TQQQ"].loc[timestamp]),
            bool(active["SOXL"].loc[timestamp]),
        )

    equity = pd.Series(equity_values, index=sessions)
    metrics = base.summarize(
        equity, exposures, fills, sizing_history, [0.0] * len(sizing_history)
    )
    return equity, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_module("rs_booster_proxy", RS_SCRIPT)
    monthly = load_module("rs_monthly_proxy", MONTHLY_SCRIPT)
    split = load_module("rs_split_proxy", SPLIT_SCRIPT)
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
        "SMH": rs.momentum_features(raw["SMH"]),
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = base.build_active(config, raw, end, slippage)
    qqq = base.benchmark(raw["QQQ"], START, end, fee, slippage)

    results = {}
    curves = {}
    for signal_key in ("SOXX", "SMH"):
        curve, metrics = simulate(
            base, monthly, frames, active, end, fee, slippage, signal_key
        )
        curves[signal_key] = curve
        results[signal_key] = {
            "metrics": metrics,
            "rolling_3y": split.rolling_distribution(curve, qqq, 3),
            "rolling_5y": split.rolling_distribution(curve, qqq, 5),
        }

    periods = {
        "2011_2018": ("2011-01-03", "2018-12-31"),
        "2019_2022": ("2019-01-01", "2022-12-30"),
        "2022_plus": ("2022-01-03", end),
        "2023_plus_observed": ("2023-01-03", end),
    }
    period_results = {
        label: {
            key: base.period_metrics(curves[key], start, finish)
            for key in ("SOXX", "SMH")
        }
        for label, (start, finish) in periods.items()
    }

    harsh = {}
    for signal_key in ("SOXX", "SMH"):
        _, metrics = simulate(
            base, monthly, frames, active, end, 0.002, 0.002, signal_key
        )
        harsh[signal_key] = metrics

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "SIGNAL_PROXY_ROBUSTNESS_NO_PRODUCTION_CHANGE",
        "rule": (
            "Frozen RS6M one-way logic; only semiconductor relative-strength "
            "proxy changes SOXX vs SMH."
        ),
        "full": results,
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
