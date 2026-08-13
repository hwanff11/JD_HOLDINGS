#!/usr/bin/env python3
"""V3.2 phase-5: conditional volatility/drawdown brakes on the robust overlay."""

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
class Rule:
    name: str
    strong: float
    bear: float
    overlay: float
    brake: str


def features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    returns = close.pct_change(fill_method=None)
    out["sma50"] = close.rolling(50).mean()
    out["sma200"] = close.rolling(200).mean()
    out["ret21"] = close / close.shift(21) - 1
    out["ret63"] = close / close.shift(63) - 1
    out["ret126"] = close / close.shift(126) - 1
    out["vol20"] = returns.rolling(20).std() * math.sqrt(252)
    out["dd252"] = close / close.rolling(252).max() - 1
    return out


def leverage_weights(leverage: float) -> dict[str, float]:
    leverage = max(0.0, min(3.0, leverage))
    if leverage <= 0:
        return {}
    if leverage <= 1:
        return {"QQQ": leverage}
    tqqq = (leverage - 1) / 2
    return {"QQQ": 1 - tqqq, "TQQQ": tqqq}


def brake_cap(row: pd.Series, style: str) -> float:
    vol = float(row["vol20"])
    dd = float(row["dd252"])
    r21 = float(row["ret21"])
    if style == "NONE":
        return 3.0
    if style == "VOL30_CAP075":
        return 0.75 if vol >= 0.30 else 3.0
    if style == "VOL35_CAP075":
        return 0.75 if vol >= 0.35 else 3.0
    if style == "VOL30_CAP05":
        return 0.50 if vol >= 0.30 else 3.0
    if style == "DD08_CAP075":
        return 0.75 if dd <= -0.08 and r21 < 0 else 3.0
    if style == "DD12_CAP075":
        return 0.75 if dd <= -0.12 and r21 < 0 else 3.0
    if style == "CONDITIONAL":
        if vol >= 0.40 or (dd <= -0.15 and r21 < 0):
            return 0.50
        if vol >= 0.30 or (dd <= -0.08 and r21 < 0):
            return 0.75
        return 3.0
    if style == "CONDITIONAL_SOFT":
        if vol >= 0.40:
            return 0.50
        if vol >= 0.32 or (dd <= -0.12 and r21 < 0):
            return 0.75
        return 3.0
    raise ValueError(style)


def base_target(row: pd.Series, rule: Rule) -> dict[str, float]:
    needed = ("sma50", "sma200", "ret21", "ret63", "ret126", "vol20", "dd252")
    if any(pd.isna(row[key]) for key in needed):
        return leverage_weights(rule.bear)
    close = float(row["close"])
    if close <= float(row["sma200"]):
        leverage = rule.bear
    else:
        strong_regime = (
            float(row["sma50"]) > float(row["sma200"])
            and float(row["ret63"]) > 0
            and float(row["ret126"]) > 0
            and float(row["vol20"]) < 0.30
        )
        leverage = rule.strong if strong_regime else 1.0
    leverage = min(leverage, brake_cap(row, rule.brake))
    return leverage_weights(leverage)


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
    return pd.Series(active, index=index).shift(1, fill_value=False)


def overlay_target(base, active_tqqq, active_soxl, overlay):
    weights = {symbol: float(base.get(symbol, 0.0)) for symbol in SYMBOLS}
    active = [
        symbol
        for symbol, enabled in (("TQQQ", active_tqqq), ("SOXL", active_soxl))
        if enabled
    ]
    if not active or overlay <= 0:
        return {key: value for key, value in weights.items() if value > 0}
    shift = min(float(overlay), weights["QQQ"])
    if shift <= 0:
        return {key: value for key, value in weights.items() if value > 0}
    weights["QQQ"] -= shift
    for symbol in active:
        weights[symbol] += shift / len(active)
    return {key: value for key, value in weights.items() if value > 0}


def rebalance(target, holdings, opens, cash, fee, slippage, trades, timestamp):
    equity = cash + sum(holdings[s] * opens[s] * (1 - fee) for s in holdings)
    desired = {}
    for symbol in holdings:
        price = opens[symbol] * (1 + slippage)
        desired[symbol] = math.floor(
            equity * float(target.get(symbol, 0.0)) / (price * (1 + fee))
        )
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
        qty = min(diff, math.floor(cash / (price * (1 + fee))))
        if qty <= 0:
            continue
        cash -= qty * price * (1 + fee)
        holdings[symbol] += qty
        trades.append((timestamp.date().isoformat(), symbol, "BUY", qty))
    return cash


def summarize(equity: pd.Series, exposures, trades) -> dict[str, object]:
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    mdd = maximum_drawdown(equity)
    sharpe, sortino = risk_adjusted_metrics(equity, 252)
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
    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = CAPITAL
    trades = []
    equity_values = []
    exposures = []
    current = None
    prior_ts = prior[-1]
    base = {"QQQ": 1.0} if benchmark else base_target(frames["QQQ"].loc[prior_ts], rule)
    pending = base if benchmark else overlay_target(
        base,
        bool(active["TQQQ"].loc[prior_ts]),
        bool(active["SOXL"].loc[prior_ts]),
        rule.overlay,
    )
    last_month = str(prior_ts.to_period("M"))
    for timestamp in sessions:
        opens = {symbol: float(frames[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS}
        closes = {symbol: float(frames[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS}
        if pending != current:
            cash = rebalance(pending, holdings, opens, cash, fee, slippage, trades, timestamp)
            current = pending
        liquidation = sum(holdings[s] * closes[s] * (1 - fee) for s in holdings)
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0)
        if not benchmark:
            month = str(timestamp.to_period("M"))
            # Normal regime is monthly, but risk brake is checked daily from completed bars.
            if month != last_month:
                base = base_target(frames["QQQ"].loc[timestamp], rule)
                last_month = month
            else:
                current_base = base_target(frames["QQQ"].loc[timestamp], rule)
                current_cap = brake_cap(frames["QQQ"].loc[timestamp], rule.brake)
                base_leverage = sum(base.values()) + 2 * float(base.get("TQQQ", 0.0))
                if current_cap < base_leverage:
                    base = current_base
            pending = overlay_target(
                base,
                bool(active["TQQQ"].loc[timestamp]),
                bool(active["SOXL"].loc[timestamp]),
                rule.overlay,
            )
    equity = pd.Series(equity_values, index=sessions)
    return summarize(equity, exposures, trades), equity


def rolling_windows(candidate: pd.Series, benchmark: pd.Series) -> list[dict[str, object]]:
    rows = []
    for start_year in range(2011, 2025):
        finish = start_year + 2
        common = candidate[
            (candidate.index.year >= start_year) & (candidate.index.year <= finish)
        ].index.intersection(
            benchmark[
                (benchmark.index.year >= start_year) & (benchmark.index.year <= finish)
            ].index
        )
        if len(common) < 600:
            continue
        years = max((common[-1] - common[0]).days / 365.2425, 1 / 365.2425)
        c = candidate.loc[common]
        b = benchmark.loc[common]
        cagr_c = (float(c.iloc[-1] / c.iloc[0]) ** (1 / years) - 1) * 100
        cagr_b = (float(b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1) * 100
        rows.append(
            {
                "period": f"{start_year}-{finish}",
                "candidate_cagr_pct": round(cagr_c, 2),
                "qqq_cagr_pct": round(cagr_b, 2),
                "excess_pp": round(cagr_c - cagr_b, 2),
            }
        )
    return rows


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
    index = raw["QQQ"].index.intersection(raw["TQQQ"].index).intersection(raw["SOXL"].index)
    active = {
        symbol: production_open_active(booster[symbol], index)
        for symbol in ("TQQQ", "SOXL")
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    qqq, qqq_equity = simulate(None, frames, active, "2011-01-03", end, fee, slippage, benchmark=True)
    brake_styles = (
        "NONE",
        "VOL30_CAP075",
        "VOL35_CAP075",
        "VOL30_CAP05",
        "DD08_CAP075",
        "DD12_CAP075",
        "CONDITIONAL",
        "CONDITIONAL_SOFT",
    )
    rules = [
        Rule(
            f"S{strong}_B{bear}_{brake}",
            strong,
            bear,
            0.05,
            brake,
        )
        for strong in (1.50, 1.625, 1.75)
        for bear in (0.25, 0.50)
        for brake in brake_styles
    ]
    rows = []
    for rule in rules:
        result, equity = simulate(rule, frames, active, "2011-01-03", end, fee, slippage)
        rolling = rolling_windows(equity, qqq_equity)
        rows.append(
            {
                "rule": rule.__dict__,
                "metrics": result,
                "rolling_win_count": sum(item["excess_pp"] > 0 for item in rolling),
                "rolling_count": len(rolling),
                "rolling": rolling,
            }
        )
    rows.sort(
        key=lambda row: (
            row["metrics"]["sharpe"],
            row["metrics"]["cagr_pct"],
            row["metrics"]["mdd_pct"],
        ),
        reverse=True,
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "qqq": qqq,
        "candidate_count": len(rows),
        "ranking_by_sharpe": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("QQQ", qqq)
    print("TOP SHARPE")
    for row in rows[:20]:
        m = row["metrics"]
        print(
            row["rule"],
            "CAGR", m["cagr_pct"],
            "MDD", m["mdd_pct"],
            "Sharpe", m["sharpe"],
            "Calmar", m["calmar"],
            "rolling", row["rolling_win_count"], "/", row["rolling_count"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
