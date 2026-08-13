#!/usr/bin/env python3
"""Final no-tuning validation for the frozen V3.2.2 RS6M candidates."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
START = "2011-01-03"
CAPITAL = 50_000.0
SYMBOLS = ("QQQ", "TQQQ", "SOXL")


def load_module(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def monthly_returns(equity: pd.Series) -> pd.Series:
    month_end = equity.groupby(equity.index.to_period("M")).last()
    return month_end.pct_change(fill_method=None).dropna()


def sharpe_from_returns(returns: pd.Series) -> float:
    values = returns.dropna().astype(float)
    if len(values) < 2 or float(values.std(ddof=1)) <= 1e-12:
        return -1e9
    return float(values.mean() / values.std(ddof=1) * math.sqrt(12))


def cscv_pbo(curves: dict[str, pd.Series], segments: int = 8) -> dict[str, object]:
    returns = pd.concat(
        {name: monthly_returns(curve) for name, curve in curves.items()}, axis=1
    ).dropna()
    n = len(returns)
    boundaries = np.linspace(0, n, segments + 1, dtype=int)
    slices = [returns.iloc[boundaries[i] : boundaries[i + 1]] for i in range(segments)]
    half = segments // 2
    test_below_median = 0
    total = 0
    selected_counts = {name: 0 for name in curves}
    test_ranks: list[float] = []

    for train_idx in itertools.combinations(range(segments), half):
        train_set = set(train_idx)
        test_idx = [i for i in range(segments) if i not in train_set]
        train = pd.concat([slices[i] for i in train_idx])
        test = pd.concat([slices[i] for i in test_idx])
        train_scores = {
            name: sharpe_from_returns(train[name]) for name in returns.columns
        }
        selected = max(train_scores, key=train_scores.get)
        selected_counts[selected] += 1
        test_scores = {
            name: sharpe_from_returns(test[name]) for name in returns.columns
        }
        ordered = sorted(test_scores, key=test_scores.get)
        rank = ordered.index(selected) + 1
        percentile = rank / len(ordered)
        test_ranks.append(percentile)
        if percentile <= 0.5:
            test_below_median += 1
        total += 1

    return {
        "segments": segments,
        "combinations": total,
        "pbo_pct": round(test_below_median / total * 100, 2),
        "median_selected_test_percentile": round(float(np.median(test_ranks)) * 100, 2),
        "train_winner_counts": selected_counts,
        "note": "CSCV-style structural-family diagnostic; not a guarantee of future performance",
    }


def block_bootstrap_excess(
    candidate: pd.Series,
    benchmark: pd.Series,
    *,
    block_months: int = 6,
    horizon_months: int = 60,
    simulations: int = 5000,
    seed: int = 32175,
) -> dict[str, float]:
    c = monthly_returns(candidate)
    b = monthly_returns(benchmark)
    common = c.index.intersection(b.index)
    excess_log = np.log1p(c.loc[common].to_numpy()) - np.log1p(b.loc[common].to_numpy())
    rng = np.random.default_rng(seed)
    block_starts = np.arange(0, len(excess_log) - block_months + 1)
    samples = np.empty(simulations)
    blocks_needed = math.ceil(horizon_months / block_months)
    for i in range(simulations):
        selected: list[float] = []
        for _ in range(blocks_needed):
            start = int(rng.choice(block_starts))
            selected.extend(excess_log[start : start + block_months])
        samples[i] = math.expm1(float(np.sum(selected[:horizon_months]))) * 100
    return {
        "outperform_probability_pct": round(float(np.mean(samples > 0)) * 100, 2),
        "median_5y_excess_pct": round(float(np.median(samples)), 2),
        "p10_5y_excess_pct": round(float(np.percentile(samples, 10)), 2),
        "p90_5y_excess_pct": round(float(np.percentile(samples, 90)), 2),
    }


def simulate_oneway_delay(
    rs,
    monthly,
    base,
    frames,
    active,
    end,
    fee,
    slippage,
    execution_delay_sessions: int,
):
    if execution_delay_sessions < 1:
        raise ValueError("execution delay must be at least one session")
    index = frames["QQQ"].index
    for symbol in ("TQQQ", "SOXL", "SOXX"):
        index = index.intersection(frames[symbol].index)
    sessions = index[(index >= pd.Timestamp(START)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if len(prior) < execution_delay_sessions:
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
    monthly_soxx = monthly.soxx_wins_6m(
        frames["QQQ"].loc[prior_ts], frames["SOXX"].loc[prior_ts]
    )
    initial_base = monthly.weights(base_lev, monthly_soxx)
    initial_target = base.overlay_target(
        initial_base,
        bool(active["TQQQ"].loc[prior_ts]),
        bool(active["SOXL"].loc[prior_ts]),
    )
    current: dict[str, float] | None = None
    last_month = str(prior_ts.to_period("M"))
    scheduled: dict[int, dict[str, float]] = {0: initial_target}
    active_target = initial_target
    signal_audit: list[dict[str, object]] = []

    for i, timestamp in enumerate(sessions):
        if i in scheduled:
            active_target = scheduled[i]
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
        if active_target != current:
            cash, count = base.rebalance_weights(
                active_target,
                holdings,
                opens,
                cash,
                0.0,
                fee,
                slippage,
                size,
            )
            fills += count
            current = active_target

        liquidation = sum(
            holdings[symbol] * closes[symbol] * (1 - fee) for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        sizing_history.append(size)
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
            if monthly_soxx and not monthly.soxx_wins_6m(qqq_row, soxx_row):
                monthly_soxx = False

        desired_base = monthly.weights(base_lev, monthly_soxx)
        desired = base.overlay_target(
            desired_base,
            bool(active["TQQQ"].loc[timestamp]),
            bool(active["SOXL"].loc[timestamp]),
        )
        apply_i = i + execution_delay_sessions
        if apply_i < len(sessions):
            scheduled[apply_i] = desired
            signal_audit.append(
                {
                    "signal_date": timestamp.date().isoformat(),
                    "apply_date": sessions[apply_i].date().isoformat(),
                    "lag_sessions": execution_delay_sessions,
                }
            )

    equity = pd.Series(equity_values, index=sessions)
    metrics = base.summarize(
        equity,
        exposures,
        fills,
        sizing_history,
        [0.0] * len(sizing_history),
    )
    assert signal_audit and min(row["lag_sessions"] for row in signal_audit) >= 1
    return equity, metrics, {
        "minimum_signal_to_execution_lag_sessions": min(
            int(row["lag_sessions"]) for row in signal_audit
        ),
        "maximum_signal_to_execution_lag_sessions": max(
            int(row["lag_sessions"]) for row in signal_audit
        ),
        "sample": signal_audit[:3],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_module("rs_booster", "research_v321_relative_strength_booster.py")
    split = load_module("rs_split", "research_v321_rs6m_split.py")
    monthly = load_module("rs_monthly", "research_v321_rs6m_monthly_lock.py")
    oneway = load_module("rs_oneway", "research_v321_rs6m_oneway_exit.py")
    strong = load_module("rs_strong", "research_v321_rs6m_strong_only.py")
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
    monthly_curve, monthly_metrics, _ = monthly.simulate(
        rs, base, frames, active, end, fee, slippage
    )
    oneway_curve, oneway_metrics, _ = oneway.simulate(
        rs, monthly, base, frames, active, end, fee, slippage
    )
    lag2_curve, lag2_metrics, lag_audit = simulate_oneway_delay(
        rs, monthly, base, frames, active, end, fee, slippage, 2
    )

    _, monthly_slip30, _ = monthly.simulate(
        rs, base, frames, active, end, fee, 0.003
    )
    _, oneway_slip30, _ = oneway.simulate(
        rs, monthly, base, frames, active, end, fee, 0.003
    )
    _, oneway_harsh, _ = oneway.simulate(
        rs, monthly, base, frames, active, end, 0.002, 0.002
    )

    eras = {
        "2011_2014": ("2011-01-03", "2014-12-31"),
        "2015_2018": ("2015-01-02", "2018-12-31"),
        "2019_2022": ("2019-01-02", "2022-12-30"),
        "2023_plus_observed": ("2023-01-03", end),
    }
    era_results = {
        label: {
            "QQQ": base.period_metrics(qqq, start, finish),
            "HWM75": base.period_metrics(base_curve, start, finish),
            "RS6M_MONTHLY": base.period_metrics(monthly_curve, start, finish),
            "RS6M_ONEWAY": base.period_metrics(oneway_curve, start, finish),
            "RS6M_ONEWAY_LAG2": base.period_metrics(lag2_curve, start, finish),
        }
        for label, (start, finish) in eras.items()
    }

    family_curves: dict[str, pd.Series] = {"HWM75": base_curve}
    for name, mode in (
        ("RS3M", "rs3m"),
        ("RS6M_WINNER", "rs6m"),
        ("CONSENSUS", "consensus"),
        ("CONSENSUS_SPLIT", "consensus_split"),
    ):
        curve, _, _ = rs.simulate(
            base,
            rs.BoosterPolicy(name, mode),
            frames,
            active,
            end,
            fee,
            slippage,
        )
        family_curves[name] = curve
    daily_curve, _, _ = split.run_split(rs, base, frames, active, end, fee, slippage)
    family_curves["RS6M_SPLIT_DAILY"] = daily_curve
    family_curves["RS6M_SPLIT_MONTHLY"] = monthly_curve
    family_curves["RS6M_ONEWAY"] = oneway_curve
    strong_curve, _, _ = strong.run_strong_only(
        monthly, rs, base, frames, active, end, fee, slippage
    )
    family_curves["RS6M_STRONG_ONLY"] = strong_curve

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "V322_FINAL_VALIDATION_NO_PRODUCTION_CHANGE",
        "frozen_candidates": ["RS6M_SPLIT_MONTHLY", "RS6M_ONEWAY"],
        "execution_audit": {
            "normal_contract": (
                "signals are evaluated after a completed daily close and the target is "
                "eligible for the following session open; no same-session execution"
            ),
            "extra_delay_test": lag_audit,
        },
        "full": {
            "QQQ": base.summarize(qqq, [1.0] * len(qqq), 1),
            "HWM75": base_metrics,
            "RS6M_SPLIT_MONTHLY": monthly_metrics,
            "RS6M_ONEWAY": oneway_metrics,
            "RS6M_ONEWAY_LAG2": lag2_metrics,
        },
        "cost_stress": {
            "slippage_0_30pct": {
                "RS6M_SPLIT_MONTHLY": monthly_slip30,
                "RS6M_ONEWAY": oneway_slip30,
            },
            "fee_0_20pct_slippage_0_20pct": {
                "RS6M_ONEWAY": oneway_harsh,
            },
        },
        "eras": era_results,
        "rolling": {
            "RS6M_MONTHLY_3Y": split.rolling_distribution(monthly_curve, qqq, 3),
            "RS6M_MONTHLY_5Y": split.rolling_distribution(monthly_curve, qqq, 5),
            "RS6M_ONEWAY_3Y": split.rolling_distribution(oneway_curve, qqq, 3),
            "RS6M_ONEWAY_5Y": split.rolling_distribution(oneway_curve, qqq, 5),
            "RS6M_ONEWAY_LAG2_5Y": split.rolling_distribution(lag2_curve, qqq, 5),
        },
        "bootstrap_5y": {
            "MONTHLY_vs_QQQ": block_bootstrap_excess(monthly_curve, qqq),
            "ONEWAY_vs_QQQ": block_bootstrap_excess(oneway_curve, qqq, seed=32176),
            "ONEWAY_vs_HWM75": block_bootstrap_excess(
                oneway_curve, base_curve, seed=32177
            ),
        },
        "structural_family_pbo": cscv_pbo(family_curves),
        "data_audit": {
            "start": str(min(oneway_curve.index).date()),
            "end": str(max(oneway_curve.index).date()),
            "soxx_signal_is_unleveraged": True,
            "relative_strength_lookback_sessions": 126,
            "candidate_parameters_retuned_in_final_stage": False,
            "observed_2023_plus_is_pristine_oos": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("FULL", report["full"])
    print("ERAS", report["eras"])
    print("ROLLING", report["rolling"])
    print("BOOTSTRAP", report["bootstrap_5y"])
    print("PBO", report["structural_family_pbo"])
    print("AUDIT", report["execution_audit"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
