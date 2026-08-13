#!/usr/bin/env python3
"""Phase-2 V3.2 screen: slow regime ladder versus QQQ buy-and-hold."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
CAPITAL = 50_000.0


@dataclass(frozen=True)
class Rule:
    name: str
    style: str
    frequency: str
    strong_leverage: float
    bear_leverage: float


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    returns = close.pct_change(fill_method=None)
    for window in (50, 150, 200):
        out[f"sma{window}"] = close.rolling(window).mean()
    for window in (21, 63, 126, 252):
        out[f"ret{window}"] = close / close.shift(window) - 1
    out["vol20"] = returns.rolling(20).std() * math.sqrt(252)
    out["dd252"] = close / close.rolling(252).max() - 1
    return out


def weights_for_leverage(leverage: float) -> dict[str, float]:
    leverage = max(0.0, min(3.0, leverage))
    if leverage == 0:
        return {}
    if leverage <= 1:
        return {"QQQ": leverage}
    tqqq = (leverage - 1) / 2
    return {"QQQ": 1 - tqqq, "TQQQ": tqqq}


def classify(row: pd.Series, style: str) -> str:
    needed = ("sma50", "sma200", "ret63", "ret126", "vol20")
    if any(pd.isna(row[key]) for key in needed):
        return "bear"
    close = float(row["close"])
    sma50 = float(row["sma50"])
    sma200 = float(row["sma200"])
    r63 = float(row["ret63"])
    r126 = float(row["ret126"])
    vol = float(row["vol20"])
    above = close > sma200
    if not above:
        return "bear"
    if style == "MOM6":
        strong = r126 > 0
    elif style == "MOM3_6":
        strong = r63 > 0 and r126 > 0
    elif style == "GOLDEN_MOM":
        strong = sma50 > sma200 and r63 > 0 and r126 > 0
    elif style == "GOLDEN_LOWVOL25":
        strong = sma50 > sma200 and r63 > 0 and r126 > 0 and vol < 0.25
    elif style == "GOLDEN_LOWVOL30":
        strong = sma50 > sma200 and r63 > 0 and r126 > 0 and vol < 0.30
    else:
        raise ValueError(style)
    return "strong" if strong else "normal"


def target(rule: Rule, row: pd.Series) -> dict[str, float]:
    regime = classify(row, rule.style)
    leverage = {
        "strong": rule.strong_leverage,
        "normal": 1.0,
        "bear": rule.bear_leverage,
    }[regime]
    return weights_for_leverage(leverage)


def bucket(frequency: str, timestamp: pd.Timestamp) -> str:
    if frequency == "weekly":
        return str(timestamp.to_period("W-FRI"))
    if frequency == "monthly":
        return str(timestamp.to_period("M"))
    raise ValueError(frequency)


def rebalance(target_weights, holdings, opens, cash, fee, slippage, trades, timestamp):
    liquidation = sum(holdings[s] * opens[s] * (1 - fee) for s in holdings)
    equity = cash + liquidation
    desired = {}
    for symbol in holdings:
        weight = float(target_weights.get(symbol, 0))
        price = opens[symbol] * (1 + slippage)
        desired[symbol] = math.floor(equity * weight / (price * (1 + fee)))
    for symbol in holdings:
        difference = desired[symbol] - holdings[symbol]
        if difference >= 0:
            continue
        qty = -difference
        price = opens[symbol] * (1 - slippage)
        cash += qty * price * (1 - fee)
        holdings[symbol] -= qty
        trades.append((timestamp.date().isoformat(), symbol, "SELL", qty))
    for symbol in holdings:
        difference = desired[symbol] - holdings[symbol]
        if difference <= 0:
            continue
        price = opens[symbol] * (1 + slippage)
        affordable = math.floor(cash / (price * (1 + fee)))
        qty = min(difference, affordable)
        if qty <= 0:
            continue
        cash -= qty * price * (1 + fee)
        holdings[symbol] += qty
        trades.append((timestamp.date().isoformat(), symbol, "BUY", qty))
    return cash


def summarize(equity: pd.Series, exposures: list[float], trades: list[tuple]) -> dict[str, object]:
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


def simulate(rule: Rule | None, frames, start, end, fee, slippage):
    index = frames["QQQ"].index.intersection(frames["TQQQ"].index)
    sessions = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if len(sessions) < 2 or prior.empty:
        raise ValueError("insufficient history")
    holdings = {"QQQ": 0, "TQQQ": 0}
    cash = CAPITAL
    trades = []
    equity_values = []
    exposures = []
    current = None
    if rule is None:
        pending = {"QQQ": 1.0}
        last_bucket = "benchmark"
    else:
        prior_ts = prior[-1]
        pending = target(rule, frames["QQQ"].loc[prior_ts])
        last_bucket = bucket(rule.frequency, prior_ts)
    for timestamp in sessions:
        opens = {s: float(frames[s].loc[timestamp, "open"]) for s in holdings}
        closes = {s: float(frames[s].loc[timestamp, "close"]) for s in holdings}
        if pending is not None and pending != current:
            cash = rebalance(pending, holdings, opens, cash, fee, slippage, trades, timestamp)
            current = pending
        pending = None
        liquidation = sum(holdings[s] * closes[s] * (1 - fee) for s in holdings)
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0)
        if rule is not None:
            next_bucket = bucket(rule.frequency, timestamp)
            if next_bucket != last_bucket:
                desired = target(rule, frames["QQQ"].loc[timestamp])
                if desired != current:
                    pending = desired
                last_bucket = next_bucket
    return summarize(pd.Series(equity_values, index=sessions), exposures, trades)


def rules() -> list[Rule]:
    output = []
    for style in ("MOM6", "MOM3_6", "GOLDEN_MOM", "GOLDEN_LOWVOL25", "GOLDEN_LOWVOL30"):
        for frequency in ("weekly", "monthly"):
            for strong in (1.5, 1.75, 2.0):
                for bear in (0.0, 0.5):
                    output.append(
                        Rule(
                            f"{style}_{frequency.upper()}_S{str(strong).replace('.', '_')}_B{str(bear).replace('.', '_')}",
                            style,
                            frequency,
                            strong,
                            bear,
                        )
                    )
    return output


def score(row, qqq_train, qqq_validation):
    train = row["train"]
    val = row["validation"]
    train_excess = float(train["cagr_pct"]) - float(qqq_train["cagr_pct"])
    val_excess = float(val["cagr_pct"]) - float(qqq_validation["cagr_pct"])
    val_mdd_adv = float(val["mdd_pct"]) - float(qqq_validation["mdd_pct"])
    sharpe_adv = float(val["sharpe"]) - float(qqq_validation["sharpe"])
    eligible = train_excess > 0 and val_excess > 0 and val_mdd_adv >= 0 and sharpe_adv >= 0
    objective = min(train_excess, val_excess) * 3 + val_mdd_adv * 0.35 + sharpe_adv * 5
    return eligible, round(objective, 4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    end = args.end or datetime.now(UTC).date().isoformat()
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=420)).isoformat()
    source = YFinanceDataSource(ROOT / "data" / "cache")
    frames = {
        symbol: build_features(source.daily(symbol, warmup, end))
        for symbol in ("QQQ", "TQQQ")
    }
    windows = {
        "train": ("2011-01-03", "2018-12-31"),
        "validation": ("2019-01-02", "2022-12-30"),
    }
    benchmark = {
        label: simulate(None, frames, start, finish, fee, slippage)
        for label, (start, finish) in windows.items()
    }
    rows = []
    for rule in rules():
        record = {"name": rule.name, "rule": rule.__dict__}
        for label, (start, finish) in windows.items():
            record[label] = simulate(rule, frames, start, finish, fee, slippage)
        record["eligible"], record["objective"] = score(
            record, benchmark["train"], benchmark["validation"]
        )
        rows.append(record)
    rows.sort(key=lambda item: item["objective"], reverse=True)
    locked = [row["name"] for row in rows if row["eligible"]][:6]
    if not locked:
        locked = [row["name"] for row in rows[:5]]
    by_name = {rule.name: rule for rule in rules()}
    confirmation_windows = {
        "oos_2023_plus": ("2023-01-03", end),
        "recent_2022_plus": ("2022-01-03", end),
        "full": ("2011-01-03", end),
    }
    confirmation = {
        "QQQ_BUY_HOLD": {
            label: simulate(None, frames, start, finish, fee, slippage)
            for label, (start, finish) in confirmation_windows.items()
        }
    }
    for name in locked:
        confirmation[name] = {
            label: simulate(by_name[name], frames, start, finish, fee, slippage)
            for label, (start, finish) in confirmation_windows.items()
        }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end": end,
        "candidate_count": len(rows),
        "benchmark_discovery": benchmark,
        "ranking": rows,
        "locked_before_oos": locked,
        "confirmation": confirmation,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("QQQ TRAIN", benchmark["train"])
    print("QQQ VALIDATION", benchmark["validation"])
    print("TOP BEFORE OOS")
    for row in rows[:12]:
        print(
            row["name"],
            "eligible=", row["eligible"],
            "train=", row["train"]["cagr_pct"], row["train"]["mdd_pct"], row["train"]["sharpe"],
            "validation=", row["validation"]["cagr_pct"], row["validation"]["mdd_pct"], row["validation"]["sharpe"],
        )
    print("LOCKED", locked)
    print("CONFIRMATION")
    for name, periods in confirmation.items():
        print(name)
        for label, result in periods.items():
            print(label, result["cagr_pct"], result["mdd_pct"], result["sharpe"], result["average_exposure_pct"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
