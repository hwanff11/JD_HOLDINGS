#!/usr/bin/env python3
"""V3.2 phase-4: production-parity audit and robustness of small JDSS overlay."""

from __future__ import annotations

import argparse
import json
import math
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
SYMBOLS = ("QQQ", "TQQQ", "SOXL")


@dataclass(frozen=True)
class Rule:
    name: str
    strong: float
    bear: float
    overlay: float
    delay_sessions: int = 0


def features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    returns = close.pct_change(fill_method=None)
    out["sma50"] = close.rolling(50).mean()
    out["sma200"] = close.rolling(200).mean()
    out["ret63"] = close / close.shift(63) - 1
    out["ret126"] = close / close.shift(126) - 1
    out["vol20"] = returns.rolling(20).std() * math.sqrt(252)
    return out


def leverage_weights(leverage: float) -> dict[str, float]:
    leverage = max(0.0, min(3.0, leverage))
    if leverage <= 0:
        return {}
    if leverage <= 1:
        return {"QQQ": leverage}
    tqqq = (leverage - 1) / 2
    return {"QQQ": 1 - tqqq, "TQQQ": tqqq}


def base_target(row: pd.Series, strong: float, bear: float) -> dict[str, float]:
    needed = ("sma50", "sma200", "ret63", "ret126", "vol20")
    if any(pd.isna(row[key]) for key in needed):
        return leverage_weights(bear)
    close = float(row["close"])
    if close <= float(row["sma200"]):
        return leverage_weights(bear)
    strong_regime = (
        float(row["sma50"]) > float(row["sma200"])
        and float(row["ret63"]) > 0
        and float(row["ret126"]) > 0
        and float(row["vol20"]) < 0.30
    )
    return leverage_weights(strong if strong_regime else 1.0)


def production_open_active(result, index: pd.DatetimeIndex) -> pd.Series:
    by_date: dict[pd.Timestamp, list[dict]] = {}
    for trade in result.trades:
        by_date.setdefault(pd.Timestamp(trade["date"]), []).append(trade)
    eod_qty = 0
    active = []
    for timestamp in index:
        trades = by_date.get(timestamp, [])
        buys = sum(int(t["quantity"]) for t in trades if t["side"] == "BUY")
        sells = sum(int(t["quantity"]) for t in trades if t["side"] == "SELL")
        open_qty = eod_qty + buys
        active.append(open_qty > 0)
        eod_qty = max(0, open_qty - sells)
    expected = int(result.open_position.get("quantity", 0))
    if eod_qty != expected:
        raise AssertionError(f"booster replay mismatch: replay={eod_qty} expected={expected}")
    return pd.Series(active, index=index)


def target_with_overlay(base, active_tqqq, active_soxl, overlay):
    weights = {symbol: float(base.get(symbol, 0.0)) for symbol in SYMBOLS}
    active = [
        symbol
        for symbol, enabled in (("TQQQ", active_tqqq), ("SOXL", active_soxl))
        if enabled
    ]
    if not active or overlay <= 0:
        return {k: v for k, v in weights.items() if v > 0}
    shift = min(float(overlay), weights["QQQ"])
    if shift <= 0:
        return {k: v for k, v in weights.items() if v > 0}
    weights["QQQ"] -= shift
    for symbol in active:
        weights[symbol] += shift / len(active)
    return {k: v for k, v in weights.items() if v > 0}


def rebalance(target, holdings, opens, cash, fee, slippage, trades, timestamp):
    liquidation = sum(holdings[s] * opens[s] * (1 - fee) for s in holdings)
    equity = cash + liquidation
    desired = {}
    for symbol in holdings:
        weight = float(target.get(symbol, 0))
        price = opens[symbol] * (1 + slippage)
        desired[symbol] = math.floor(equity * weight / (price * (1 + fee)))
    for symbol in holdings:
        diff = desired[symbol] - holdings[symbol]
        if diff >= 0:
            continue
        qty = -diff
        price = opens[symbol] * (1 - slippage)
        cash += qty * price * (1 - fee)
        holdings[symbol] -= qty
        trades.append((timestamp.date().isoformat(), symbol, "SELL", qty))
    for symbol in holdings:
        diff = desired[symbol] - holdings[symbol]
        if diff <= 0:
            continue
        price = opens[symbol] * (1 + slippage)
        affordable = math.floor(cash / (price * (1 + fee)))
        qty = min(diff, affordable)
        if qty <= 0:
            continue
        cash -= qty * price * (1 + fee)
        holdings[symbol] += qty
        trades.append((timestamp.date().isoformat(), symbol, "BUY", qty))
    return cash


def metrics(equity, exposures, trades):
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, 252)
    mdd = maximum_drawdown(equity)
    monthly = (
        equity.groupby(equity.index.to_period("M"))
        .last()
        .pct_change(fill_method=None)
        .dropna()
    )
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round((final / CAPITAL - 1) * 100, 2),
        "cagr_pct": round(((final / CAPITAL) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round((((final / CAPITAL) ** (1 / years) - 1) / abs(mdd)), 3),
        "average_exposure_pct": round(sum(exposures) / len(exposures) * 100, 2),
        "trade_fills": len(trades),
        "worst_month_pct": round(float(monthly.min()) * 100, 2) if not monthly.empty else 0.0,
        "annual_returns_pct": {
            str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
            for year, group in equity.groupby(equity.index.year)
        },
    }


def simulate(rule, frames, active, start, end, fee, slippage, benchmark=False):
    index = frames["QQQ"].index
    for symbol in SYMBOLS[1:]:
        index = index.intersection(frames[symbol].index)
    sessions = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if len(sessions) < 2 or prior.empty:
        raise ValueError("insufficient history")
    act = {}
    for symbol in ("TQQQ", "SOXL"):
        series = active[symbol].reindex(index).fillna(False).astype(bool)
        if rule and rule.delay_sessions:
            series = series.shift(rule.delay_sessions, fill_value=False)
        act[symbol] = series
    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = CAPITAL
    trades = []
    equity_values = []
    exposures = []
    current = None
    prior_ts = prior[-1]
    if benchmark:
        base = {"QQQ": 1.0}
    else:
        base = base_target(frames["QQQ"].loc[prior_ts], rule.strong, rule.bear)
    pending = (
        base
        if benchmark
        else target_with_overlay(
            base,
            bool(act["TQQQ"].loc[prior_ts]),
            bool(act["SOXL"].loc[prior_ts]),
            rule.overlay,
        )
    )
    last_month = str(prior_ts.to_period("M"))
    for timestamp in sessions:
        opens = {s: float(frames[s].loc[timestamp, "open"]) for s in SYMBOLS}
        closes = {s: float(frames[s].loc[timestamp, "close"]) for s in SYMBOLS}
        if pending != current:
            cash = rebalance(
                pending, holdings, opens, cash, fee, slippage, trades, timestamp
            )
            current = pending
        liquidation = sum(holdings[s] * closes[s] * (1 - fee) for s in holdings)
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0)
        if not benchmark:
            month = str(timestamp.to_period("M"))
            if month != last_month:
                base = base_target(
                    frames["QQQ"].loc[timestamp], rule.strong, rule.bear
                )
                last_month = month
            pending = target_with_overlay(
                base,
                bool(act["TQQQ"].loc[timestamp]),
                bool(act["SOXL"].loc[timestamp]),
                rule.overlay,
            )
    equity = pd.Series(equity_values, index=sessions)
    return metrics(equity, exposures, trades), equity


def rolling_three_year(candidate, benchmark):
    rows = []
    years = sorted(set(candidate.index.year) & set(benchmark.index.year))
    for start_year in years:
        end_year = start_year + 2
        c = candidate[
            (candidate.index.year >= start_year) & (candidate.index.year <= end_year)
        ]
        b = benchmark[
            (benchmark.index.year >= start_year) & (benchmark.index.year <= end_year)
        ]
        common = c.index.intersection(b.index)
        if len(common) < 600:
            continue
        c = c.loc[common]
        b = b.loc[common]
        period_years = max((common[-1] - common[0]).days / 365.2425, 1 / 365.2425)
        c_cagr = (float(c.iloc[-1] / c.iloc[0]) ** (1 / period_years) - 1) * 100
        b_cagr = (float(b.iloc[-1] / b.iloc[0]) ** (1 / period_years) - 1) * 100
        rows.append(
            {
                "period": f"{start_year}-{end_year}",
                "candidate_cagr_pct": round(c_cagr, 2),
                "qqq_cagr_pct": round(b_cagr, 2),
                "excess_pp": round(c_cagr - b_cagr, 2),
            }
        )
    return rows


def bootstrap_monthly(candidate, benchmark, seed=311):
    c = candidate.groupby(candidate.index.to_period("M")).last().pct_change(fill_method=None)
    b = benchmark.groupby(benchmark.index.to_period("M")).last().pct_change(fill_method=None)
    pair = pd.concat([c.rename("c"), b.rename("b")], axis=1).dropna()
    diff = np.log1p(pair["c"].to_numpy()) - np.log1p(pair["b"].to_numpy())
    rng = np.random.default_rng(seed)
    horizon = 60
    sims = []
    for _ in range(5000):
        idx = rng.integers(0, len(diff), size=horizon)
        sims.append(float(np.exp(diff[idx].sum()) - 1))
    arr = np.asarray(sims)
    return {
        "months": len(diff),
        "five_year_outperformance_probability_pct": round(
            float((arr > 0).mean()) * 100, 2
        ),
        "five_year_excess_return_median_pct": round(float(np.median(arr)) * 100, 2),
        "five_year_excess_return_p10_pct": round(float(np.quantile(arr, 0.10)) * 100, 2),
        "five_year_excess_return_p90_pct": round(float(np.quantile(arr, 0.90)) * 100, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
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
        symbol: features(raw[symbol]) if symbol == "QQQ" else raw[symbol]
        for symbol in SYMBOLS
    }
    engine = StrategyBacktestEngine(config)
    booster = {
        "TQQQ": engine.run(
            "TQQQ",
            raw["TQQQ"],
            raw["SPY"],
            raw["QQQ"],
            start="2011-01-03",
            end=end,
            slippage=float(config.backtest.default_slippage),
        ),
        "SOXL": engine.run(
            "SOXL",
            raw["SOXL"],
            raw["SPY"],
            raw["QQQ"],
            start="2011-01-03",
            end=end,
            slippage=float(config.backtest.default_slippage),
            sector_data={"SOXX": raw["SOXX"], "SMH": raw["SMH"]},
        ),
    }
    index = (
        raw["QQQ"].index.intersection(raw["TQQQ"].index).intersection(raw["SOXL"].index)
    )
    active = {
        symbol: production_open_active(booster[symbol], index)
        for symbol in ("TQQQ", "SOXL")
    }
    baseline_fee = float(config.global_.buy_fee)
    baseline_slip = float(config.backtest.default_slippage)
    benchmark_metrics, benchmark_equity = simulate(
        None,
        frames,
        active,
        "2011-01-03",
        end,
        baseline_fee,
        baseline_slip,
        benchmark=True,
    )

    centers = [
        Rule("V32_BALANCED", 1.50, 0.50, 0.05, 0),
        Rule("V32_RETURN", 1.75, 0.50, 0.05, 0),
        Rule("V32_BALANCED_DELAY1", 1.50, 0.50, 0.05, 1),
        Rule("V32_RETURN_DELAY1", 1.75, 0.50, 0.05, 1),
    ]
    center_results = {}
    center_equity = {}
    for rule in centers:
        result, equity = simulate(
            rule,
            frames,
            active,
            "2011-01-03",
            end,
            baseline_fee,
            baseline_slip,
        )
        center_results[rule.name] = result
        center_equity[rule.name] = equity

    neighborhood = []
    for strong in (1.50, 1.625, 1.75, 1.875):
        for bear in (0.25, 0.50, 0.75):
            for overlay in (0.025, 0.05, 0.075, 0.10):
                rule = Rule(f"S{strong}_B{bear}_O{overlay}", strong, bear, overlay, 1)
                result, _ = simulate(
                    rule,
                    frames,
                    active,
                    "2011-01-03",
                    end,
                    baseline_fee,
                    baseline_slip,
                )
                neighborhood.append({"rule": rule.__dict__, "metrics": result})

    stress = {}
    for rule in centers:
        stress[rule.name] = {}
        for label, fee, slip in (
            ("baseline", baseline_fee, baseline_slip),
            ("slip_20bp", baseline_fee, 0.002),
            ("slip_30bp", baseline_fee, 0.003),
            ("fee20_slip20", 0.002, 0.002),
        ):
            result, _ = simulate(rule, frames, active, "2011-01-03", end, fee, slip)
            stress[rule.name][label] = result

    rolling = {
        name: rolling_three_year(eq, benchmark_equity)
        for name, eq in center_equity.items()
    }
    bootstrap = {
        name: bootstrap_monthly(eq, benchmark_equity)
        for name, eq in center_equity.items()
    }
    annual_comparison = {}
    for name, result in center_results.items():
        annual_comparison[name] = {
            year: {
                "candidate_pct": value,
                "qqq_pct": benchmark_metrics["annual_returns_pct"].get(year),
                "excess_pp": round(
                    value - benchmark_metrics["annual_returns_pct"].get(year, value), 2
                ),
            }
            for year, value in result["annual_returns_pct"].items()
        }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "parity": {
            "soxl_sector_benchmarks": ["SOXX", "SMH"],
            "overlay_active_definition": "post-open BUY, pre-intraday TP sell",
            "conservative_delay_variant": True,
        },
        "qqq": benchmark_metrics,
        "centers": center_results,
        "neighborhood": neighborhood,
        "stress": stress,
        "rolling_three_year": rolling,
        "bootstrap_monthly": bootstrap,
        "annual_comparison": annual_comparison,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("QQQ", benchmark_metrics)
    for name, result in center_results.items():
        wins = sum(row["excess_pp"] > 0 for row in rolling[name])
        print(
            name,
            result,
            "rolling_wins",
            wins,
            "/",
            len(rolling[name]),
            "bootstrap",
            bootstrap[name],
        )
    print("STRESS")
    for name, cases in stress.items():
        print(
            name,
            {
                label: (m["cagr_pct"], m["mdd_pct"], m["sharpe"])
                for label, m in cases.items()
            },
        )
    top = sorted(
        neighborhood,
        key=lambda row: (row["metrics"]["cagr_pct"], row["metrics"]["mdd_pct"]),
        reverse=True,
    )[:12]
    print("NEIGHBORHOOD TOP")
    for row in top:
        print(
            row["rule"],
            row["metrics"]["cagr_pct"],
            row["metrics"]["mdd_pct"],
            row["metrics"]["sharpe"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
