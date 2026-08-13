#!/usr/bin/env python3
"""Research simple HWM75 drawdown controls and diagnose recent underperformance."""

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
class RiskPolicy:
    name: str
    dd_steps: tuple[tuple[float, float], ...] = ()
    profit_floor_fraction: float = 0.0


def load_base():
    spec = importlib.util.spec_from_file_location("controlled_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load controlled compounding base module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def risk_multiplier(policy: RiskPolicy, drawdown: float) -> float:
    multiplier = 1.0
    for threshold, candidate in sorted(policy.dd_steps):
        if drawdown >= threshold:
            multiplier = candidate
    return multiplier


def risk_state(policy: RiskPolicy, drawdown: float) -> str:
    state = "NORMAL"
    for threshold, multiplier in sorted(policy.dd_steps):
        if drawdown >= threshold:
            state = f"DD{int(threshold * 100)}_X{multiplier:.2f}"
    return state


def hwm75_base(equity: float, high_water: float, locked_cash: float) -> float:
    target = CAPITAL + 0.75 * max(0.0, high_water - CAPITAL)
    return max(0.0, min(target, equity - locked_cash))


def simulate(
    base,
    policy: RiskPolicy,
    frames: dict[str, pd.DataFrame],
    active: dict[str, pd.Series],
    end: str,
    fee: float,
    slippage: float,
    *,
    disable_overlay: bool = False,
) -> tuple[pd.Series, dict[str, object]]:
    index = frames["QQQ"].index
    for symbol in SYMBOLS[1:]:
        index = index.intersection(frames[symbol].index)
    sessions = index[(index >= pd.Timestamp(START)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if prior.empty:
        raise ValueError("warmup history is insufficient")

    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = CAPITAL
    locked_cash = 0.0
    high_water = CAPITAL
    equity_values: list[float] = []
    exposures: list[float] = []
    sizing_history: list[float] = []
    locked_history: list[float] = []
    leverage_history: list[float] = []
    overlay_history: list[bool] = []
    risk_state_history: list[str] = []
    fills = 0

    prior_ts = prior[-1]
    base_lev = base.v321_leverage(frames["QQQ"].loc[prior_ts])
    prior_tqqq = False if disable_overlay else bool(active["TQQQ"].loc[prior_ts])
    prior_soxl = False if disable_overlay else bool(active["SOXL"].loc[prior_ts])
    pending = base.overlay_target(
        base.leverage_weights(base_lev),
        prior_tqqq,
        prior_soxl,
    )
    pending_lev = base_lev
    pending_overlay = prior_tqqq or prior_soxl
    current: dict[str, float] | None = None
    current_risk_state = "INIT"
    last_month = str(prior_ts.to_period("M"))
    force_rebalance = True

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
        drawdown = max(0.0, 1.0 - open_equity / high_water)
        state = risk_state(policy, drawdown)
        multiplier = risk_multiplier(policy, drawdown)

        floor_target = policy.profit_floor_fraction * max(
            0.0,
            high_water - CAPITAL,
        )
        if floor_target > locked_cash + 0.01:
            locked_cash = floor_target
            force_rebalance = True

        size = hwm75_base(open_equity, high_water, locked_cash) * multiplier
        size = max(0.0, min(size, open_equity - locked_cash))
        if pending != current or force_rebalance or state != current_risk_state:
            cash, count = base.rebalance_weights(
                pending,
                holdings,
                opens,
                cash,
                locked_cash,
                fee,
                slippage,
                size,
            )
            fills += count
            current = pending
            current_risk_state = state
            force_rebalance = False

        liquidation = sum(
            holdings[symbol] * closes[symbol] * (1 - fee)
            for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        sizing_history.append(size)
        locked_history.append(locked_cash)
        leverage_history.append(pending_lev)
        overlay_history.append(pending_overlay)
        risk_state_history.append(state)
        high_water = max(high_water, equity)

        row = frames["QQQ"].loc[timestamp]
        month = str(timestamp.to_period("M"))
        if month != last_month:
            base_lev = base.v321_leverage(row)
            last_month = month
        elif float(row["vol20"]) >= 0.30 and base_lev > 0.5:
            base_lev = 0.5

        use_tqqq = False if disable_overlay else bool(active["TQQQ"].loc[timestamp])
        use_soxl = False if disable_overlay else bool(active["SOXL"].loc[timestamp])
        pending = base.overlay_target(
            base.leverage_weights(base_lev),
            use_tqqq,
            use_soxl,
        )
        pending_lev = base_lev
        pending_overlay = use_tqqq or use_soxl

    equity = pd.Series(equity_values, index=sessions)
    metrics = base.summarize(
        equity,
        exposures,
        fills,
        sizing_history,
        locked_history,
    )
    diagnostics = {
        "metrics": metrics,
        "leverage": pd.Series(leverage_history, index=sessions),
        "overlay": pd.Series(overlay_history, index=sessions),
        "risk_state": pd.Series(risk_state_history, index=sessions),
        "exposure": pd.Series(exposures, index=sessions),
    }
    return equity, diagnostics


def rolling_compare(candidate: pd.Series, benchmark: pd.Series, years: int) -> dict[str, float]:
    common = candidate.index.intersection(benchmark.index)
    excess: list[float] = []
    for start in common[::21]:
        target = start + pd.DateOffset(years=years)
        ends = common[common >= target]
        if ends.empty:
            break
        end = ends[0]
        span = max((end - start).days / 365.2425, 1 / 365.2425)
        candidate_cagr = (float(candidate.loc[end] / candidate.loc[start]) ** (1 / span) - 1) * 100
        benchmark_cagr = (float(benchmark.loc[end] / benchmark.loc[start]) ** (1 / span) - 1) * 100
        excess.append(candidate_cagr - benchmark_cagr)
    values = np.asarray(excess)
    return {
        "win_rate_pct": round(float(np.mean(values > 0)) * 100, 2),
        "median_excess_pp": round(float(np.median(values)), 2),
        "worst_excess_pp": round(float(np.min(values)), 2),
    }


def year_return(equity: pd.Series, year: int) -> float:
    annual = equity.groupby(equity.index.year).last()
    years = list(annual.index)
    if year not in years:
        return math.nan
    pos = years.index(year)
    previous = CAPITAL if pos == 0 else float(annual.iloc[pos - 1])
    return (float(annual.loc[year]) / previous - 1) * 100


def diagnose_year(
    candidate: pd.Series,
    benchmark: pd.Series,
    diagnostics: dict[str, object],
    year: int,
) -> dict[str, object]:
    leverage = diagnostics["leverage"]
    overlay = diagnostics["overlay"]
    exposure = diagnostics["exposure"]
    mask = leverage.index.year == year
    lev_year = leverage.loc[mask]
    overlay_year = overlay.loc[mask]
    exposure_year = exposure.loc[mask]

    common = candidate.index.intersection(benchmark.index)
    c_ret = candidate.loc[common].pct_change(fill_method=None)
    b_ret = benchmark.loc[common].pct_change(fill_method=None)
    relative_log = np.log1p(c_ret) - np.log1p(b_ret)
    lev_aligned = leverage.reindex(common)
    by_leverage: dict[str, float] = {}
    for value in (0.5, 1.0, 1.25, 1.5):
        selected = relative_log[lev_aligned == value].dropna()
        by_leverage[f"{value:.2f}x"] = round(float(selected.sum()) * 100, 2)

    counts = lev_year.value_counts().sort_index()
    return {
        "strategy_return_pct": round(year_return(candidate, year), 2),
        "qqq_return_pct": round(year_return(benchmark, year), 2),
        "excess_pp": round(
            year_return(candidate, year) - year_return(benchmark, year),
            2,
        ),
        "average_exposure_pct": round(float(exposure_year.mean()) * 100, 2),
        "leverage_days": {
            f"{float(key):.2f}x": int(value)
            for key, value in counts.items()
        },
        "overlay_days": int(overlay_year.sum()),
        "log_excess_contribution_pct_by_leverage": by_leverage,
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

    policies = (
        RiskPolicy("HWM75_BASE"),
        RiskPolicy("DD15_X080", ((0.15, 0.80),)),
        RiskPolicy("DD10_X090_DD20_X075", ((0.10, 0.90), (0.20, 0.75))),
        RiskPolicy("DD10_X085_DD20_X070", ((0.10, 0.85), (0.20, 0.70))),
        RiskPolicy("PROFIT_FLOOR25", (), 0.25),
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
        metrics = diag["metrics"]
        results[policy.name] = {
            "metrics": metrics,
            "rolling_3y_vs_qqq": rolling_compare(equity, qqq, 3),
            "rolling_5y_vs_qqq": rolling_compare(equity, qqq, 5),
        }

    baseline = curves["HWM75_BASE"]
    no_overlay, _ = simulate(
        base,
        policies[0],
        frames,
        active,
        end,
        fee,
        slippage,
        disable_overlay=True,
    )
    full100, full100_metrics = base.run_policy(
        base.SizingPolicy("FULL_100", "current", 1.0),
        frames,
        active,
        end,
        fee,
        slippage,
    )

    recent_diagnosis = {
        str(year): diagnose_year(
            baseline,
            qqq,
            diagnostics["HWM75_BASE"],
            year,
        )
        for year in (2022, 2023, 2024, 2025, 2026)
    }
    recent_diagnosis["2025_attribution"] = {
        "hwm75_return_pct": round(year_return(baseline, 2025), 2),
        "full100_same_strategy_return_pct": round(year_return(full100, 2025), 2),
        "hwm75_no_overlay_return_pct": round(year_return(no_overlay, 2025), 2),
        "qqq_return_pct": round(year_return(qqq, 2025), 2),
        "sizing_drag_pp": round(
            year_return(baseline, 2025) - year_return(full100, 2025),
            2,
        ),
        "overlay_effect_pp": round(
            year_return(baseline, 2025) - year_return(no_overlay, 2025),
            2,
        ),
        "core_regime_gap_vs_qqq_pp": round(
            year_return(no_overlay, 2025) - year_return(qqq, 2025),
            2,
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
        ("slip30", fee, 0.003),
    ):
        qqq_cost = base.benchmark(
            raw["QQQ"],
            START,
            end,
            scenario_fee,
            scenario_slippage,
        )
        rows: dict[str, object] = {
            "QQQ": base.summarize(qqq_cost, [1.0] * len(qqq_cost), 1)
        }
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
        "status": "HWM75_RISK_CONTROL_RESEARCH_NO_PRODUCTION_CHANGE",
        "rules": {
            "v321_strategy_frozen": True,
            "hwm_fraction_frozen": 0.75,
            "2023_plus_pristine_oos": False,
        },
        "full": results,
        "periods": period_results,
        "recent_diagnosis": recent_diagnosis,
        "cost_stress": cost_results,
        "full100_reference": full100_metrics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("FULL", results)
    print("RECENT", recent_diagnosis)
    print("PERIODS", period_results)
    print("COST", cost_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
