#!/usr/bin/env python3
"""Final research-only validation for the frozen V3.2 shadow candidate."""

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
from scipy.stats import rankdata

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
OVERLAY_PATH = ROOT / "scripts" / "research_v32_jdss_overlay.py"


def load_overlay_module():
    spec = importlib.util.spec_from_file_location("v32_overlay", OVERLAY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load V3.2 overlay research module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def monthly_returns(equity: pd.Series) -> pd.Series:
    return equity.groupby(equity.index.to_period("M")).last().pct_change(fill_method=None).dropna()


def simulate_fixed_leverage(mod, leverage, frames, active, start, end, fee, slippage):
    index = frames["QQQ"].index.intersection(frames["TQQQ"].index)
    sessions = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if len(sessions) < 2 or prior.empty:
        raise ValueError("insufficient history")
    holdings = {symbol: 0 for symbol in mod.SYMBOLS}
    cash = mod.CAPITAL
    trades = []
    equity_values = []
    exposures = []
    target = mod.leverage_weights(leverage)
    pending = target
    last_month = str(prior[-1].to_period("M"))
    for timestamp in sessions:
        opens = {s: float(frames[s].loc[timestamp, "open"]) for s in mod.SYMBOLS}
        closes = {s: float(frames[s].loc[timestamp, "close"]) for s in mod.SYMBOLS}
        if pending is not None:
            cash = mod.rebalance(
                target, holdings, opens, cash, fee, slippage, trades, timestamp
            )
            pending = None
        liquidation = sum(holdings[s] * closes[s] * (1 - fee) for s in holdings)
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        month = str(timestamp.to_period("M"))
        if month != last_month:
            pending = target
            last_month = month
    equity = pd.Series(equity_values, index=sessions)
    return mod.summarize(equity, exposures, trades), equity


def cscv_pbo(return_matrix: pd.DataFrame, slices: int = 8) -> dict[str, object]:
    clean = return_matrix.dropna(how="any")
    blocks = [np.asarray(block, dtype=int) for block in np.array_split(np.arange(len(clean)), slices)]
    lambdas = []
    selections = []
    for chosen in itertools.combinations(range(slices), slices // 2):
        train_idx = np.concatenate([blocks[i] for i in chosen])
        test_idx = np.concatenate([blocks[i] for i in range(slices) if i not in chosen])
        train = clean.iloc[train_idx]
        test = clean.iloc[test_idx]
        train_sharpe = train.mean() / train.std(ddof=1)
        test_sharpe = test.mean() / test.std(ddof=1)
        selected = str(train_sharpe.idxmax())
        ranks = rankdata(test_sharpe.to_numpy(), method="average")
        rank_map = dict(zip(test_sharpe.index, ranks, strict=True))
        w = (float(rank_map[selected]) - 0.5) / len(test_sharpe)
        w = min(max(w, 1e-9), 1 - 1e-9)
        lambdas.append(math.log(w / (1 - w)))
        selections.append(selected)
    counts = pd.Series(selections).value_counts().to_dict()
    return {
        "slices": slices,
        "combinations": len(lambdas),
        "pbo_pct": round(100 * sum(value < 0 for value in lambdas) / len(lambdas), 2),
        "median_logit_rank": round(float(np.median(lambdas)), 4),
        "selection_counts": {str(key): int(value) for key, value in counts.items()},
    }


def moving_block_bootstrap_excess(
    candidate: pd.Series,
    benchmark: pd.Series,
    block_months: int = 6,
    horizon_months: int = 60,
    simulations: int = 5000,
    seed: int = 320,
) -> dict[str, float | int]:
    pair = pd.concat(
        [monthly_returns(candidate).rename("candidate"), monthly_returns(benchmark).rename("qqq")],
        axis=1,
    ).dropna()
    excess = np.log1p(pair["candidate"].to_numpy()) - np.log1p(pair["qqq"].to_numpy())
    starts = np.arange(0, max(1, len(excess) - block_months + 1))
    rng = np.random.default_rng(seed)
    outcomes = []
    blocks_needed = math.ceil(horizon_months / block_months)
    for _ in range(simulations):
        pieces = []
        for _ in range(blocks_needed):
            start = int(rng.choice(starts))
            pieces.extend(excess[start : start + block_months])
        total = float(np.sum(pieces[:horizon_months]))
        outcomes.append(math.exp(total) - 1)
    arr = np.asarray(outcomes)
    return {
        "observed_months": int(len(excess)),
        "block_months": block_months,
        "horizon_months": horizon_months,
        "simulations": simulations,
        "outperformance_probability_pct": round(float((arr > 0).mean()) * 100, 2),
        "excess_return_p10_pct": round(float(np.quantile(arr, 0.10)) * 100, 2),
        "excess_return_median_pct": round(float(np.median(arr)) * 100, 2),
        "excess_return_p90_pct": round(float(np.quantile(arr, 0.90)) * 100, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mod = load_overlay_module()
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
        symbol: mod.features(raw[symbol]) if symbol == "QQQ" else raw[symbol]
        for symbol in mod.SYMBOLS
    }
    engine = mod.StrategyBacktestEngine(config)
    booster = {
        "TQQQ": engine.run(
            "TQQQ", raw["TQQQ"], raw["SPY"], raw["QQQ"],
            start="2011-01-03", end=end,
            slippage=float(config.backtest.default_slippage),
        ),
        "SOXL": engine.run(
            "SOXL", raw["SOXL"], raw["SPY"], raw["QQQ"],
            start="2011-01-03", end=end,
            slippage=float(config.backtest.default_slippage),
            sector_data={"SOXX": raw["SOXX"], "SMH": raw["SMH"]},
        ),
    }
    index = (
        raw["QQQ"].index.intersection(raw["TQQQ"].index).intersection(raw["SOXL"].index)
    )
    active = {
        symbol: mod.production_open_active(booster[symbol], index)
        for symbol in ("TQQQ", "SOXL")
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    start = "2011-01-03"
    qqq_metrics, qqq_equity = mod.simulate(
        None, frames, active, start, end, fee, slippage, benchmark=True
    )

    frozen = mod.Rule("V32_SHADOW_FROZEN", 1.50, 0.50, 0.05, "VOL30_CAP05")
    no_overlay = mod.Rule("V32_NO_JDSS_OVERLAY", 1.50, 0.50, 0.00, "VOL30_CAP05")
    frozen_metrics, frozen_equity = mod.simulate(
        frozen, frames, active, start, end, fee, slippage
    )
    no_overlay_metrics, no_overlay_equity = mod.simulate(
        no_overlay, frames, active, start, end, fee, slippage
    )

    fixed = {}
    fixed_equity = {}
    for leverage in (1.25, 1.50, 1.75):
        result, equity = simulate_fixed_leverage(
            mod, leverage, frames, active, start, end, fee, slippage
        )
        fixed[str(leverage)] = result
        fixed_equity[str(leverage)] = equity

    brake_styles = (
        "NONE", "VOL30_CAP075", "VOL35_CAP075", "VOL30_CAP05",
        "DD08_CAP075", "DD12_CAP075", "CONDITIONAL", "CONDITIONAL_SOFT",
    )
    family_returns = {}
    for strong in (1.50, 1.625, 1.75):
        for bear in (0.25, 0.50):
            for brake in brake_styles:
                name = f"S{strong}_B{bear}_{brake}"
                rule = mod.Rule(name, strong, bear, 0.05, brake)
                _, equity = mod.simulate(rule, frames, active, start, end, fee, slippage)
                family_returns[name] = monthly_returns(equity)
    matrix = pd.DataFrame(family_returns)
    pbo = cscv_pbo(matrix, 8)
    block_bootstrap = moving_block_bootstrap_excess(frozen_equity, qqq_equity)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "frozen_rule": frozen.__dict__,
        "qqq": qqq_metrics,
        "v32_frozen": frozen_metrics,
        "v32_without_jdss_overlay": no_overlay_metrics,
        "fixed_monthly_leverage_benchmarks": fixed,
        "overlay_incremental": {
            "cagr_pp": round(
                float(frozen_metrics["cagr_pct"]) - float(no_overlay_metrics["cagr_pct"]), 2
            ),
            "mdd_pp": round(
                float(frozen_metrics["mdd_pct"]) - float(no_overlay_metrics["mdd_pct"]), 2
            ),
            "sharpe": round(
                float(frozen_metrics["sharpe"]) - float(no_overlay_metrics["sharpe"]), 3
            ),
        },
        "cscv_pbo_phase5_family": pbo,
        "moving_block_bootstrap_vs_qqq": block_bootstrap,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
