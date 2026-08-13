#!/usr/bin/env python3
"""Robustness research for frozen V3.2.1-RETURN controlled compounding."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
CAPITAL = 50_000.0
START = "2011-01-03"
SYMBOLS = ("QQQ", "TQQQ", "SOXL")


@dataclass(frozen=True)
class SizingPolicy:
    name: str
    mode: str
    fraction: float = 1.0
    skim_frequency: str = "none"


def features(qqq: pd.DataFrame, spy: pd.DataFrame) -> pd.DataFrame:
    out = qqq.copy()
    close = out["close"].astype(float)
    returns = close.pct_change(fill_method=None)
    spy_close = spy["close"].astype(float).reindex(out.index)
    out["sma50"] = close.rolling(50).mean()
    out["sma200"] = close.rolling(200).mean()
    out["ret21"] = close / close.shift(21) - 1
    out["ret63"] = close / close.shift(63) - 1
    out["ret126"] = close / close.shift(126) - 1
    out["vol20"] = returns.rolling(20).std() * math.sqrt(252)
    out["sma200_slope21"] = out["sma200"] / out["sma200"].shift(21) - 1
    out["rel_spy63"] = (close / close.shift(63)) / (spy_close / spy_close.shift(63)) - 1
    return out


def row_ready(row: pd.Series) -> bool:
    needed = (
        "sma50",
        "sma200",
        "ret21",
        "ret63",
        "ret126",
        "vol20",
        "sma200_slope21",
        "rel_spy63",
    )
    return not any(pd.isna(row[key]) for key in needed)


def trend_vote(row: pd.Series) -> bool:
    votes = sum(
        (
            float(row["ret21"]) > 0,
            float(row["ret63"]) > 0,
            float(row["ret126"]) > 0,
            float(row["sma50"]) > float(row["sma200"]),
            float(row["sma200_slope21"]) > 0,
        )
    )
    return votes >= 3


def v321_leverage(row: pd.Series) -> float:
    if not row_ready(row):
        return 1.0
    if float(row["vol20"]) >= 0.30:
        return 0.5
    if float(row["close"]) <= float(row["sma200"]):
        return 1.0
    strong = (
        float(row["sma50"]) > float(row["sma200"])
        and float(row["ret63"]) > 0
        and float(row["ret126"]) > 0
    )
    if strong:
        return 1.5
    if trend_vote(row):
        return 1.25
    return 1.0


def leverage_weights(leverage: float) -> dict[str, float]:
    if leverage <= 1.0:
        return {"QQQ": max(0.0, leverage)}
    tqqq = (leverage - 1.0) / 2.0
    return {"QQQ": 1.0 - tqqq, "TQQQ": tqqq}


def production_open_active(result, index: pd.DatetimeIndex) -> pd.Series:
    by_date: dict[pd.Timestamp, list[dict]] = defaultdict(list)
    for trade in result.trades:
        by_date[pd.Timestamp(trade["date"])].append(trade)
    eod_qty = 0
    active: list[bool] = []
    for timestamp in index:
        trades = by_date.get(timestamp, [])
        buys = sum(int(item["quantity"]) for item in trades if item["side"] == "BUY")
        sells = sum(int(item["quantity"]) for item in trades if item["side"] == "SELL")
        open_qty = eod_qty + buys
        active.append(open_qty > 0)
        eod_qty = max(0, open_qty - sells)
    expected = int(result.open_position.get("quantity", 0))
    if eod_qty != expected:
        raise AssertionError(f"booster replay mismatch: replay={eod_qty} expected={expected}")
    return pd.Series(active, index=index).shift(1, fill_value=False)


def overlay_target(
    base: dict[str, float], active_tqqq: bool, active_soxl: bool
) -> dict[str, float]:
    weights = {symbol: float(base.get(symbol, 0.0)) for symbol in SYMBOLS}
    active = [
        symbol
        for symbol, enabled in (("TQQQ", active_tqqq), ("SOXL", active_soxl))
        if enabled
    ]
    shift = min(0.05, weights["QQQ"]) if active else 0.0
    weights["QQQ"] -= shift
    for symbol in active:
        weights[symbol] += shift / len(active)
    return {symbol: weight for symbol, weight in weights.items() if weight > 0}


def period_end_sets(index: pd.DatetimeIndex) -> dict[str, set[pd.Timestamp]]:
    values = pd.Series(index=index, data=index)
    return {
        "monthly": set(values.groupby(index.to_period("M")).last().tolist()),
        "quarterly": set(values.groupby(index.to_period("Q")).last().tolist()),
        "annual": set(values.groupby(index.to_period("Y")).last().tolist()),
    }


def rebalance_weights(
    target: dict[str, float],
    holdings: dict[str, int],
    opens: dict[str, float],
    cash: float,
    locked_cash: float,
    fee: float,
    slippage: float,
    sizing_equity: float,
) -> tuple[float, int]:
    desired: dict[str, int] = {}
    for symbol in holdings:
        price = opens[symbol] * (1 + slippage)
        desired[symbol] = math.floor(
            sizing_equity * float(target.get(symbol, 0.0)) / (price * (1 + fee))
        )

    fills = 0
    for symbol in holdings:
        diff = desired[symbol] - holdings[symbol]
        if diff >= 0:
            continue
        qty = -diff
        price = opens[symbol] * (1 - slippage)
        cash += qty * price * (1 - fee)
        holdings[symbol] -= qty
        fills += 1

    for symbol in holdings:
        diff = desired[symbol] - holdings[symbol]
        if diff <= 0:
            continue
        price = opens[symbol] * (1 + slippage)
        tradable_cash = max(0.0, cash - locked_cash)
        qty = min(diff, math.floor(tradable_cash / (price * (1 + fee))))
        if qty <= 0:
            continue
        cash -= qty * price * (1 + fee)
        holdings[symbol] += qty
        fills += 1
    return cash, fills


def sizing_base(
    policy: SizingPolicy,
    equity: float,
    high_water: float,
    annual_ratchet: float,
    locked_cash: float,
) -> float:
    if policy.mode == "fixed":
        base = CAPITAL
    elif policy.mode == "current":
        base = CAPITAL + policy.fraction * max(0.0, equity - CAPITAL)
    elif policy.mode == "hwm":
        base = CAPITAL + policy.fraction * max(0.0, high_water - CAPITAL)
    elif policy.mode == "annual":
        base = annual_ratchet
    elif policy.mode == "lockbox":
        base = equity - locked_cash
    else:
        raise ValueError(policy.mode)
    return max(0.0, min(base, equity - locked_cash))


def simulate(
    policy: SizingPolicy,
    frames: dict[str, pd.DataFrame],
    active: dict[str, pd.Series],
    end: str,
    fee: float,
    slippage: float,
) -> tuple[pd.Series, list[float], int, list[float], list[float]]:
    index = frames["QQQ"].index
    for symbol in SYMBOLS[1:]:
        index = index.intersection(frames[symbol].index)
    sessions = index[(index >= pd.Timestamp(START)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if prior.empty:
        raise ValueError("V3.2.1 warmup history is insufficient")

    period_ends = period_end_sets(sessions)
    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = CAPITAL
    locked_cash = 0.0
    lock_reference = CAPITAL
    equity_values: list[float] = []
    exposures: list[float] = []
    sizing_history: list[float] = []
    locked_history: list[float] = []
    fills = 0
    high_water = CAPITAL
    annual_ratchet = CAPITAL
    ratchet_reference = CAPITAL

    prior_ts = prior[-1]
    base_lev = v321_leverage(frames["QQQ"].loc[prior_ts])
    pending = overlay_target(
        leverage_weights(base_lev),
        bool(active["TQQQ"].loc[prior_ts]),
        bool(active["SOXL"].loc[prior_ts]),
    )
    current: dict[str, float] | None = None
    last_month = str(prior_ts.to_period("M"))
    last_year = prior_ts.year
    force_sizing_rebalance = True

    for timestamp in sessions:
        opens = {symbol: float(frames[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS}
        closes = {symbol: float(frames[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS}
        open_equity = cash + sum(
            holdings[symbol] * opens[symbol] * (1 - fee) for symbol in SYMBOLS
        )

        if timestamp.year != last_year:
            if policy.mode == "annual":
                gain = max(0.0, high_water - ratchet_reference)
                annual_ratchet += policy.fraction * gain
                ratchet_reference = high_water
                force_sizing_rebalance = True
            last_year = timestamp.year

        size = sizing_base(
            policy, open_equity, high_water, annual_ratchet, locked_cash
        )
        if pending != current or force_sizing_rebalance:
            cash, count = rebalance_weights(
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
            force_sizing_rebalance = False

        liquidation = sum(
            holdings[symbol] * closes[symbol] * (1 - fee) for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        sizing_history.append(size)
        locked_history.append(locked_cash)
        high_water = max(high_water, equity)

        if (
            policy.mode == "lockbox"
            and timestamp in period_ends[policy.skim_frequency]
            and equity > lock_reference
        ):
            new_profit = equity - lock_reference
            newly_locked = (1.0 - policy.fraction) * new_profit
            locked_cash += newly_locked
            lock_reference = equity
            force_sizing_rebalance = newly_locked > 0

        row = frames["QQQ"].loc[timestamp]
        month = str(timestamp.to_period("M"))
        if month != last_month:
            base_lev = v321_leverage(row)
            last_month = month
        elif float(row["vol20"]) >= 0.30 and base_lev > 0.5:
            base_lev = 0.5

        pending = overlay_target(
            leverage_weights(base_lev),
            bool(active["TQQQ"].loc[timestamp]),
            bool(active["SOXL"].loc[timestamp]),
        )

    return (
        pd.Series(equity_values, index=sessions),
        exposures,
        fills,
        sizing_history,
        locked_history,
    )


def benchmark(
    frame: pd.DataFrame,
    start: str,
    end: str,
    fee: float,
    slippage: float,
) -> pd.Series:
    sessions = frame.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    buy_price = float(sessions.iloc[0]["open"]) * (1 + slippage)
    quantity = math.floor(CAPITAL / (buy_price * (1 + fee)))
    cash = CAPITAL - quantity * buy_price * (1 + fee)
    return pd.Series(
        cash + quantity * sessions["close"].astype(float) * (1 - fee),
        index=sessions.index,
    )


def calendar_returns(equity: pd.Series) -> dict[str, float]:
    year_ends = equity.groupby(equity.index.year).last()
    result: dict[str, float] = {}
    previous = CAPITAL
    for year, value in year_ends.items():
        result[str(year)] = round((float(value) / previous - 1) * 100, 2)
        previous = float(value)
    return result


def summarize(
    equity: pd.Series,
    exposures: list[float],
    fills: int,
    sizing_history: list[float] | None = None,
    locked_history: list[float] | None = None,
) -> dict[str, object]:
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    mdd = maximum_drawdown(equity)
    sharpe, sortino = risk_adjusted_metrics(equity, 252)
    result: dict[str, object] = {
        "final_equity": round(final, 2),
        "total_return_pct": round((final / CAPITAL - 1) * 100, 2),
        "cagr_pct": round(((final / CAPITAL) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round((((final / CAPITAL) ** (1 / years) - 1) / abs(mdd)), 3),
        "average_exposure_pct": round(sum(exposures) / len(exposures) * 100, 2),
        "trade_fills": fills,
        "annual_returns_pct": calendar_returns(equity),
    }
    if sizing_history:
        result["average_sizing_base"] = round(sum(sizing_history) / len(sizing_history), 2)
        result["maximum_sizing_base"] = round(max(sizing_history), 2)
        result["final_sizing_base"] = round(sizing_history[-1], 2)
    if locked_history:
        result["final_locked_cash"] = round(locked_history[-1], 2)
        result["maximum_locked_cash"] = round(max(locked_history), 2)
    return result


def period_metrics(equity: pd.Series, start: str, end: str) -> dict[str, float]:
    sample = equity.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    years = max((sample.index[-1] - sample.index[0]).days / 365.2425, 1 / 365.2425)
    mdd = maximum_drawdown(sample)
    sharpe, _ = risk_adjusted_metrics(sample, 252)
    return {
        "cagr_pct": round(
            ((float(sample.iloc[-1] / sample.iloc[0])) ** (1 / years) - 1) * 100,
            2,
        ),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
    }


def rolling_cagr(equity: pd.Series, years: int) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for start in equity.index[::21]:
        target = start + pd.DateOffset(years=years)
        positions = equity.index[equity.index >= target]
        if positions.empty:
            break
        end = positions[0]
        span = max((end - start).days / 365.2425, 1 / 365.2425)
        cagr = (float(equity.loc[end] / equity.loc[start]) ** (1 / span) - 1) * 100
        rows.append(
            {
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "cagr_pct": cagr,
            }
        )
    worst = min(rows, key=lambda row: float(row["cagr_pct"]))
    return {
        "start": worst["start"],
        "end": worst["end"],
        "cagr_pct": round(float(worst["cagr_pct"]), 2),
    }


def monthly_returns(equity: pd.Series) -> pd.Series:
    month_ends = equity.groupby(equity.index.to_period("M")).last()
    previous = pd.Series([CAPITAL], index=[month_ends.index[0] - 1])
    extended = pd.concat([previous, month_ends])
    return extended.pct_change(fill_method=None).dropna().iloc[-len(month_ends) :]


def monthly_sharpe(values: np.ndarray) -> float:
    if len(values) < 2 or float(np.std(values, ddof=1)) == 0:
        return -math.inf
    return float(np.mean(values) / np.std(values, ddof=1) * math.sqrt(12))


def cscv_pbo(returns: pd.DataFrame, slices: int = 8) -> dict[str, object]:
    n = len(returns)
    edges = np.linspace(0, n, slices + 1, dtype=int)
    blocks = [np.arange(edges[i], edges[i + 1]) for i in range(slices)]
    failures = 0
    total = 0
    selections: Counter[str] = Counter()
    for train_blocks in itertools.combinations(range(slices), slices // 2):
        train_set = set(train_blocks)
        train_idx = np.concatenate([blocks[i] for i in train_blocks])
        test_idx = np.concatenate([blocks[i] for i in range(slices) if i not in train_set])
        train_scores = {
            column: monthly_sharpe(returns[column].to_numpy()[train_idx])
            for column in returns.columns
        }
        selected = max(train_scores, key=train_scores.get)
        selections[selected] += 1
        test_scores = {
            column: monthly_sharpe(returns[column].to_numpy()[test_idx])
            for column in returns.columns
        }
        ranked = sorted(test_scores, key=test_scores.get, reverse=True)
        rank = ranked.index(selected) + 1
        failures += rank > len(ranked) / 2
        total += 1
    return {
        "pbo_pct": round(failures / total * 100, 2),
        "combinations": total,
        "train_selection_counts": dict(selections),
    }


def block_bootstrap_excess(
    candidate: pd.Series,
    benchmark_equity: pd.Series,
    *,
    seed: int = 32175,
    simulations: int = 5000,
    block: int = 6,
    horizon: int = 60,
) -> dict[str, float]:
    c = monthly_returns(candidate)
    b = monthly_returns(benchmark_equity)
    common = c.index.intersection(b.index)
    paired = np.column_stack([c.loc[common].to_numpy(), b.loc[common].to_numpy()])
    rng = np.random.default_rng(seed)
    excess: list[float] = []
    for _ in range(simulations):
        chunks: list[np.ndarray] = []
        while sum(len(chunk) for chunk in chunks) < horizon:
            start = int(rng.integers(0, max(1, len(paired) - block + 1)))
            chunks.append(paired[start : start + block])
        sample = np.concatenate(chunks, axis=0)[:horizon]
        c_growth = float(np.prod(1 + sample[:, 0]))
        b_growth = float(np.prod(1 + sample[:, 1]))
        excess.append((c_growth / b_growth - 1) * 100)
    values = np.asarray(excess)
    return {
        "outperform_probability_pct": round(float(np.mean(values > 0)) * 100, 2),
        "median_5y_excess_pct": round(float(np.median(values)), 2),
        "p10_5y_excess_pct": round(float(np.quantile(values, 0.10)), 2),
        "p90_5y_excess_pct": round(float(np.quantile(values, 0.90)), 2),
    }


def policy_family() -> tuple[SizingPolicy, ...]:
    return (
        SizingPolicy("FIXED_0", "fixed", 0.0),
        SizingPolicy("CURRENT_50", "current", 0.50),
        SizingPolicy("CURRENT_67", "current", 0.67),
        SizingPolicy("CURRENT_70", "current", 0.70),
        SizingPolicy("CURRENT_75", "current", 0.75),
        SizingPolicy("CURRENT_80", "current", 0.80),
        SizingPolicy("CURRENT_83", "current", 0.83),
        SizingPolicy("FULL_100", "current", 1.00),
        SizingPolicy("HWM_75", "hwm", 0.75),
        SizingPolicy("ANNUAL_75", "annual", 0.75),
        SizingPolicy("LOCKBOX_M_70", "lockbox", 0.70, "monthly"),
        SizingPolicy("LOCKBOX_M_75", "lockbox", 0.75, "monthly"),
        SizingPolicy("LOCKBOX_M_80", "lockbox", 0.80, "monthly"),
        SizingPolicy("LOCKBOX_Q_75", "lockbox", 0.75, "quarterly"),
        SizingPolicy("LOCKBOX_Y_75", "lockbox", 0.75, "annual"),
    )


def build_active(config, raw, end: str, slippage: float) -> dict[str, pd.Series]:
    engine = StrategyBacktestEngine(config)
    boosters = {
        "TQQQ": engine.run(
            "TQQQ",
            raw["TQQQ"],
            raw["SPY"],
            raw["QQQ"],
            start=START,
            end=end,
            slippage=slippage,
        ),
        "SOXL": engine.run(
            "SOXL",
            raw["SOXL"],
            raw["SPY"],
            raw["QQQ"],
            start=START,
            end=end,
            slippage=slippage,
            sector_data={"SOXX": raw["SOXX"], "SMH": raw["SMH"]},
        ),
    }
    common_index = (
        raw["QQQ"].index.intersection(raw["TQQQ"].index).intersection(raw["SOXL"].index)
    )
    return {
        symbol: production_open_active(boosters[symbol], common_index)
        for symbol in ("TQQQ", "SOXL")
    }


def run_policy(policy, frames, active, end, fee, slippage):
    equity, exposures, fills, sizing, locked = simulate(
        policy, frames, active, end, fee, slippage
    )
    return equity, summarize(equity, exposures, fills, sizing, locked)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = load_config(ROOT / "strategy.yaml")
    end = args.end or datetime.now(UTC).date().isoformat()
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=420)).isoformat()
    source = YFinanceDataSource(ROOT / "data" / "cache")
    raw = {
        symbol: source.daily(symbol, warmup, end)
        for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    }
    frames = {
        "QQQ": features(raw["QQQ"], raw["SPY"]),
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = build_active(config, raw, end, slippage)

    qqq_equity = benchmark(raw["QQQ"], START, end, fee, slippage)
    qqq_metrics = summarize(qqq_equity, [1.0] * len(qqq_equity), 1)

    curves: dict[str, pd.Series] = {}
    results: dict[str, object] = {}
    for policy in policy_family():
        equity, metrics = run_policy(policy, frames, active, end, fee, slippage)
        curves[policy.name] = equity
        results[policy.name] = {
            "policy": policy.__dict__,
            "metrics": metrics,
            "beats_qqq_cagr": float(metrics["cagr_pct"])
            > float(qqq_metrics["cagr_pct"]),
            "beats_qqq_mdd": float(metrics["mdd_pct"]) > float(qqq_metrics["mdd_pct"]),
            "beats_qqq_sharpe": float(metrics["sharpe"])
            > float(qqq_metrics["sharpe"]),
            "worst_3y": rolling_cagr(equity, 3),
            "worst_5y": rolling_cagr(equity, 5),
        }

    periods = {
        "train_2011_2018": ("2011-01-03", "2018-12-31"),
        "validation_2019_2022": ("2019-01-01", "2022-12-30"),
        "recent_2022_plus": ("2022-01-03", end),
        "observed_2023_plus": ("2023-01-03", end),
    }
    selected = ("CURRENT_75", "LOCKBOX_M_75", "HWM_75", "ANNUAL_75", "FULL_100")
    period_results = {
        period: {
            "qqq": period_metrics(qqq_equity, start, finish),
            **{
                name: period_metrics(curves[name], start, finish)
                for name in selected
            },
        }
        for period, (start, finish) in periods.items()
    }

    cost_scenarios = {
        "base": (fee, slippage),
        "slip20": (fee, 0.002),
        "slip30": (fee, 0.003),
        "harsh_fee20_slip20": (0.002, 0.002),
    }
    cost_results: dict[str, object] = {}
    for scenario, (scenario_fee, scenario_slippage) in cost_scenarios.items():
        qqq = benchmark(raw["QQQ"], START, end, scenario_fee, scenario_slippage)
        qqq_m = summarize(qqq, [1.0] * len(qqq), 1)
        rows: dict[str, object] = {"qqq": qqq_m}
        for name in ("CURRENT_75", "LOCKBOX_M_75", "HWM_75", "FULL_100"):
            policy = next(item for item in policy_family() if item.name == name)
            _, metrics = run_policy(
                policy,
                frames,
                active,
                end,
                scenario_fee,
                scenario_slippage,
            )
            rows[name] = metrics
        cost_results[scenario] = rows

    family_monthly = pd.DataFrame(
        {
            name: monthly_returns(curve)
            for name, curve in curves.items()
        }
    ).dropna()
    pbo = cscv_pbo(family_monthly, slices=8)
    bootstrap = {
        name: block_bootstrap_excess(curves[name], qqq_equity)
        for name in ("CURRENT_75", "LOCKBOX_M_75", "HWM_75", "FULL_100")
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "research_status": "CONTROLLED_COMPOUNDING_ROBUSTNESS_NO_PRODUCTION_CHANGE",
        "contract": {
            "capital": CAPITAL,
            "fee": fee,
            "slippage": slippage,
            "strategy": "frozen V3.2.1-RETURN signals/leverage/JDSS overlay",
            "external_loss_topup": False,
            "observed_2023_plus_is_not_pristine_oos": True,
        },
        "qqq": qqq_metrics,
        "candidates": results,
        "periods": period_results,
        "cost_stress": cost_results,
        "sizing_family_pbo": pbo,
        "moving_block_bootstrap_vs_qqq": bootstrap,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("QQQ", qqq_metrics)
    for name, result in results.items():
        m = result["metrics"]
        print(
            name,
            "CAGR", m["cagr_pct"],
            "MDD", m["mdd_pct"],
            "Sharpe", m["sharpe"],
            "Calmar", m["calmar"],
            "worst3", result["worst_3y"]["cagr_pct"],
            "worst5", result["worst_5y"]["cagr_pct"],
            "locked", m.get("final_locked_cash", 0),
        )
    print("PERIODS", period_results)
    print("COST", cost_results)
    print("PBO", pbo)
    print("BOOTSTRAP", bootstrap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
