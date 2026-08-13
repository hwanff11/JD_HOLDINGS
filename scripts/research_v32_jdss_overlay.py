#!/usr/bin/env python3
"""Phase-3 V3.2 screen: QQQ/regime core plus production JDSS booster overlay."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
CAPITAL = 50_000.0
SYMBOLS = ("QQQ", "TQQQ", "SOXL")


@dataclass(frozen=True)
class OverlayRule:
    name: str
    base: str
    overlay: float
    use_soxl: bool


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


def golden_base(row: pd.Series, strong_leverage: float) -> dict[str, float]:
    needed = ("sma50", "sma200", "ret63", "ret126", "vol20")
    if any(pd.isna(row[key]) for key in needed):
        return {"QQQ": 0.5}
    close = float(row["close"])
    if close <= float(row["sma200"]):
        return {"QQQ": 0.5}
    strong = (
        float(row["sma50"]) > float(row["sma200"])
        and float(row["ret63"]) > 0
        and float(row["ret126"]) > 0
        and float(row["vol20"]) < 0.30
    )
    return leverage_weights(strong_leverage if strong else 1.0)


def base_target(name: str, row: pd.Series) -> dict[str, float]:
    if name == "QQQ":
        return {"QQQ": 1.0}
    if name == "GOLDEN_1_5":
        return golden_base(row, 1.5)
    if name == "GOLDEN_1_75":
        return golden_base(row, 1.75)
    raise ValueError(name)


def build_active_series(result, index: pd.DatetimeIndex) -> pd.Series:
    events: dict[pd.Timestamp, list[dict]] = {}
    for trade in result.trades:
        events.setdefault(pd.Timestamp(trade["date"]), []).append(trade)
    qty = 0
    values = []
    delayed_sells = 0
    for timestamp in index:
        # BUY fills are next-open executions based on prior completed bars, so they may
        # activate the overlay on the same date without look-ahead.
        for trade in events.get(timestamp, []):
            if trade["side"] == "BUY":
                qty += int(trade["quantity"])
        if delayed_sells:
            qty = max(0, qty - delayed_sells)
            delayed_sells = 0
        values.append(qty > 0)
        # TP sells may occur intraday. Delay their overlay deactivation to next day.
        for trade in events.get(timestamp, []):
            if trade["side"] == "SELL":
                delayed_sells += int(trade["quantity"])
    return pd.Series(values, index=index)


def with_overlay(base: dict[str, float], active_tqqq: bool, active_soxl: bool, rule: OverlayRule):
    weights = {symbol: float(base.get(symbol, 0.0)) for symbol in SYMBOLS}
    active = []
    if active_tqqq:
        active.append("TQQQ")
    if rule.use_soxl and active_soxl:
        active.append("SOXL")
    if not active or rule.overlay <= 0:
        return {k: v for k, v in weights.items() if v > 0}
    shift = min(float(rule.overlay), weights["QQQ"])
    if shift <= 0:
        return {k: v for k, v in weights.items() if v > 0}
    weights["QQQ"] -= shift
    each = shift / len(active)
    for symbol in active:
        weights[symbol] += each
    return {k: v for k, v in weights.items() if v > 0}


def rebalance(target, holdings, opens, cash, fee, slippage, trades, timestamp):
    liquidation = sum(holdings[s] * opens[s] * (1 - fee) for s in holdings)
    equity = cash + liquidation
    desired = {}
    for symbol in holdings:
        weight = float(target.get(symbol, 0.0))
        buy_price = opens[symbol] * (1 + slippage)
        desired[symbol] = math.floor(equity * weight / (buy_price * (1 + fee)))
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


def summarize(equity, exposures, trades):
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    final = float(equity.iloc[-1])
    sharpe, sortino = risk_adjusted_metrics(equity, 252)
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round((final / CAPITAL - 1) * 100, 2),
        "cagr_pct": round(((final / CAPITAL) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "average_exposure_pct": round(sum(exposures) / len(exposures) * 100, 2),
        "trade_fills": len(trades),
        "annual_returns_pct": {
            str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
            for year, group in equity.groupby(equity.index.year)
        },
    }


def simulate(rule, frames, active, start, end, fee, slippage):
    index = frames["QQQ"].index
    for symbol in SYMBOLS[1:]:
        index = index.intersection(frames[symbol].index)
    sessions = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if len(sessions) < 2 or prior.empty:
        raise ValueError("insufficient history")
    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = CAPITAL
    trades = []
    equity_values = []
    exposures = []
    current = None
    prior_ts = prior[-1]
    base = base_target(rule.base, frames["QQQ"].loc[prior_ts])
    pending = with_overlay(base, bool(active["TQQQ"].loc[prior_ts]), bool(active["SOXL"].loc[prior_ts]), rule)
    last_month = str(prior_ts.to_period("M"))
    for timestamp in sessions:
        opens = {s: float(frames[s].loc[timestamp, "open"]) for s in SYMBOLS}
        closes = {s: float(frames[s].loc[timestamp, "close"]) for s in SYMBOLS}
        if pending != current:
            cash = rebalance(pending, holdings, opens, cash, fee, slippage, trades, timestamp)
            current = pending
        liquidation = sum(holdings[s] * closes[s] * (1 - fee) for s in holdings)
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0)
        month = str(timestamp.to_period("M"))
        if month != last_month:
            base = base_target(rule.base, frames["QQQ"].loc[timestamp])
            last_month = month
        desired = with_overlay(
            base,
            bool(active["TQQQ"].loc[timestamp]),
            bool(active["SOXL"].loc[timestamp]),
            rule,
        )
        pending = desired
    return summarize(pd.Series(equity_values, index=sessions), exposures, trades)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    end = args.end or datetime.now(UTC).date().isoformat()
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=420)).isoformat()
    source = YFinanceDataSource(ROOT / "data" / "cache")
    raw = {symbol: source.daily(symbol, warmup, end) for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX")}
    frames = {symbol: features(raw[symbol]) if symbol == "QQQ" else raw[symbol] for symbol in SYMBOLS}
    engine = StrategyBacktestEngine(config)
    booster = {
        "TQQQ": engine.run("TQQQ", raw["TQQQ"], raw["SPY"], raw["QQQ"], start="2011-01-03", end=end, slippage=float(config.backtest.default_slippage)),
        "SOXL": engine.run("SOXL", raw["SOXL"], raw["SPY"], raw["QQQ"], start="2011-01-03", end=end, slippage=float(config.backtest.default_slippage), sector_data={"SOXX": raw["SOXX"]}),
    }
    index = raw["QQQ"].index.intersection(raw["TQQQ"].index).intersection(raw["SOXL"].index)
    active = {symbol: build_active_series(booster[symbol], index) for symbol in ("TQQQ", "SOXL")}
    rules = []
    for base in ("QQQ", "GOLDEN_1_5", "GOLDEN_1_75"):
        for overlay in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
            rules.append(OverlayRule(f"{base}_JDSS_BOTH_{int(overlay * 100)}", base, overlay, True))
            rules.append(OverlayRule(f"{base}_JDSS_TQQQ_{int(overlay * 100)}", base, overlay, False))
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    discovery_windows = {
        "train": ("2011-01-03", "2018-12-31"),
        "validation": ("2019-01-02", "2022-12-30"),
    }
    benchmark_rule = OverlayRule("QQQ_BH", "QQQ", 0.0, False)
    benchmark = {
        label: simulate(benchmark_rule, frames, active, start, finish, fee, slippage)
        for label, (start, finish) in discovery_windows.items()
    }
    rows = []
    for rule in rules:
        record = {"name": rule.name, "rule": rule.__dict__}
        for label, (start, finish) in discovery_windows.items():
            record[label] = simulate(rule, frames, active, start, finish, fee, slippage)
        train_excess = record["train"]["cagr_pct"] - benchmark["train"]["cagr_pct"]
        val_excess = record["validation"]["cagr_pct"] - benchmark["validation"]["cagr_pct"]
        val_mdd_adv = record["validation"]["mdd_pct"] - benchmark["validation"]["mdd_pct"]
        val_sharpe_adv = record["validation"]["sharpe"] - benchmark["validation"]["sharpe"]
        record["eligible"] = train_excess > 0 and val_excess > 0 and val_mdd_adv >= 0
        record["objective"] = round(min(train_excess, val_excess) * 3 + val_mdd_adv * 0.3 + val_sharpe_adv * 4, 4)
        rows.append(record)
    rows.sort(key=lambda row: row["objective"], reverse=True)
    locked = [row["name"] for row in rows if row["eligible"]][:6]
    if not locked:
        locked = [row["name"] for row in rows[:5]]
    by_name = {rule.name: rule for rule in rules}
    confirmation_windows = {
        "oos_2023_plus": ("2023-01-03", end),
        "recent_2022_plus": ("2022-01-03", end),
        "full": ("2011-01-03", end),
    }
    confirmation = {
        "QQQ_BUY_HOLD": {
            label: simulate(benchmark_rule, frames, active, start, finish, fee, slippage)
            for label, (start, finish) in confirmation_windows.items()
        }
    }
    for name in locked:
        confirmation[name] = {
            label: simulate(by_name[name], frames, active, start, finish, fee, slippage)
            for label, (start, finish) in confirmation_windows.items()
        }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_count": len(rows),
        "benchmark_discovery": benchmark,
        "ranking": rows,
        "locked_before_oos": locked,
        "confirmation": confirmation,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("TOP BEFORE OOS")
    for row in rows[:12]:
        print(row["name"], row["eligible"], row["train"]["cagr_pct"], row["train"]["mdd_pct"], row["validation"]["cagr_pct"], row["validation"]["mdd_pct"], row["validation"]["sharpe"])
    print("LOCKED", locked)
    for name, periods in confirmation.items():
        print(name)
        for label, result in periods.items():
            print(label, result["cagr_pct"], result["mdd_pct"], result["sharpe"], result["average_exposure_pct"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
