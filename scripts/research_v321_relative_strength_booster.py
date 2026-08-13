#!/usr/bin/env python3
"""Test relative-strength selection only for the leverage sleeve of V3.2.1 HWM75."""

from __future__ import annotations

import argparse
import importlib.util
import json
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
class BoosterPolicy:
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


def momentum_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    out["ret63"] = close / close.shift(63) - 1
    out["ret126"] = close / close.shift(126) - 1
    return out


def soxx_wins(policy: BoosterPolicy, qqq_row: pd.Series, soxx_row: pd.Series) -> bool:
    values = (
        qqq_row.get("ret63"),
        qqq_row.get("ret126"),
        soxx_row.get("ret63"),
        soxx_row.get("ret126"),
    )
    if any(pd.isna(value) for value in values):
        return False

    q63 = float(qqq_row["ret63"])
    q126 = float(qqq_row["ret126"])
    s63 = float(soxx_row["ret63"])
    s126 = float(soxx_row["ret126"])
    if policy.mode == "rs3m":
        return s63 > 0 and s63 > q63
    if policy.mode == "rs6m":
        return s126 > 0 and s126 > q126
    if policy.mode in {"consensus", "consensus_split"}:
        return s63 > 0 and s126 > 0 and s63 > q63 and s126 > q126
    return False


def leverage_weights(
    policy: BoosterPolicy,
    leverage: float,
    qqq_row: pd.Series,
    soxx_row: pd.Series,
) -> dict[str, float]:
    if leverage <= 1.0:
        return {"QQQ": max(0.0, leverage)}
    leveraged_sleeve = (leverage - 1.0) / 2.0
    weights = {"QQQ": 1.0 - leveraged_sleeve}
    if policy.mode == "base" or not soxx_wins(policy, qqq_row, soxx_row):
        weights["TQQQ"] = leveraged_sleeve
        return weights
    if policy.mode == "consensus_split":
        weights["TQQQ"] = leveraged_sleeve / 2.0
        weights["SOXL"] = leveraged_sleeve / 2.0
        return weights
    weights["SOXL"] = leveraged_sleeve
    return weights


def simulate(base, policy, frames, active, end, fee, slippage):
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
    booster_soxl_history: list[bool] = []
    fills = 0

    prior_ts = prior[-1]
    base_lev = base.v321_leverage(frames["QQQ"].loc[prior_ts])
    pending_base = leverage_weights(
        policy,
        base_lev,
        frames["QQQ"].loc[prior_ts],
        frames["SOXX"].loc[prior_ts],
    )
    pending = base.overlay_target(
        pending_base,
        bool(active["TQQQ"].loc[prior_ts]),
        bool(active["SOXL"].loc[prior_ts]),
    )
    pending_soxl = float(pending_base.get("SOXL", 0.0)) > 0
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
        booster_soxl_history.append(pending_soxl)
        high_water = max(high_water, equity)

        row = frames["QQQ"].loc[timestamp]
        month = str(timestamp.to_period("M"))
        if month != last_month:
            base_lev = base.v321_leverage(row)
            last_month = month
        elif float(row["vol20"]) >= 0.30 and base_lev > 0.5:
            base_lev = 0.5

        next_base = leverage_weights(
            policy,
            base_lev,
            row,
            frames["SOXX"].loc[timestamp],
        )
        use_tqqq = bool(active["TQQQ"].loc[timestamp])
        use_soxl = bool(active["SOXL"].loc[timestamp])
        pending = base.overlay_target(next_base, use_tqqq, use_soxl)
        pending_soxl = float(next_base.get("SOXL", 0.0)) > 0

    equity = pd.Series(equity_values, index=sessions)
    metrics = base.summarize(
        equity,
        exposures,
        fills,
        sizing_history,
        [0.0] * len(sizing_history),
    )
    return equity, metrics, pd.Series(booster_soxl_history, index=sessions)


def rolling_compare(candidate: pd.Series, benchmark: pd.Series, years: int):
    common = candidate.index.intersection(benchmark.index)
    excess: list[float] = []
    for start in common[::21]:
        target = start + pd.DateOffset(years=years)
        ends = common[common >= target]
        if ends.empty:
            break
        end = ends[0]
        span = max((end - start).days / 365.2425, 1 / 365.2425)
        cagr_c = (float(candidate.loc[end] / candidate.loc[start]) ** (1 / span) - 1) * 100
        cagr_b = (float(benchmark.loc[end] / benchmark.loc[start]) ** (1 / span) - 1) * 100
        excess.append(cagr_c - cagr_b)
    values = np.asarray(excess)
    return {
        "win_rate_pct": round(float(np.mean(values > 0)) * 100, 2),
        "median_excess_pp": round(float(np.median(values)), 2),
        "worst_excess_pp": round(float(np.min(values)), 2),
    }


def yearly_soxl_share(series: pd.Series) -> dict[str, float]:
    return {
        str(year): round(float(group.mean()) * 100, 2)
        for year, group in series.groupby(series.index.year)
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
        "SOXX": momentum_features(raw["SOXX"]),
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = base.build_active(config, raw, end, slippage)
    qqq = base.benchmark(raw["QQQ"], START, end, fee, slippage)
    qqq_metrics = base.summarize(qqq, [1.0] * len(qqq), 1)

    policies = (
        BoosterPolicy("BASE_TQQQ", "base"),
        BoosterPolicy("RS_3M", "rs3m"),
        BoosterPolicy("RS_6M", "rs6m"),
        BoosterPolicy("RS_3M6M_CONSENSUS", "consensus"),
        BoosterPolicy("RS_CONSENSUS_SPLIT", "consensus_split"),
    )
    curves: dict[str, pd.Series] = {}
    results: dict[str, object] = {"QQQ": qqq_metrics}
    for policy in policies:
        equity, metrics, soxl_state = simulate(
            base,
            policy,
            frames,
            active,
            end,
            fee,
            slippage,
        )
        curves[policy.name] = equity
        results[policy.name] = {
            "metrics": metrics,
            "rolling_3y_vs_qqq": rolling_compare(equity, qqq, 3),
            "rolling_5y_vs_qqq": rolling_compare(equity, qqq, 5),
            "soxl_booster_days_pct_by_year": yearly_soxl_share(soxl_state),
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
            _, metrics, _ = simulate(
                base,
                policy,
                frames,
                active,
                end,
                scenario_fee,
                scenario_slippage,
            )
            rows[policy.name] = metrics
        cost_results[label] = rows

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RELATIVE_STRENGTH_BOOSTER_RESEARCH_NO_PRODUCTION_CHANGE",
        "frozen": {
            "v321_core_timing": True,
            "hwm_fraction": 0.75,
            "jdss_overlay": 0.05,
            "only_leverage_sleeve_rotates": True,
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
