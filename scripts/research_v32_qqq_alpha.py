#!/usr/bin/env python3
"""Research-only V3.2 discovery against QQQ buy-and-hold."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
INITIAL_CAPITAL = 50_000.0
SYMBOLS = ("QQQ", "TQQQ", "SOXX", "SOXL")


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    frequency: str
    target: Callable[[dict[str, pd.Series]], dict[str, float]]


def features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    close = result["close"].astype(float)
    returns = close.pct_change(fill_method=None)
    for window in (50, 150, 200):
        result[f"sma{window}"] = close.rolling(window).mean()
    for window in (21, 63, 126):
        result[f"ret{window}"] = close / close.shift(window) - 1.0
    result["vol20"] = returns.rolling(20).std() * math.sqrt(252)
    result["dd252"] = close / close.rolling(252).max() - 1.0
    return result


def leverage_weights(underlying: str, leveraged: str, leverage: float) -> dict[str, float]:
    leverage = max(0.0, min(3.0, float(leverage)))
    if leverage <= 0:
        return {}
    if leverage <= 1:
        return {underlying: leverage}
    levered_weight = (leverage - 1.0) / 2.0
    return {underlying: 1.0 - levered_weight, leveraged: levered_weight}


def trend_ok(row: pd.Series, sma: int = 200) -> bool:
    required = ("close", f"sma{sma}", "ret126")
    return not any(pd.isna(row[key]) for key in required) and (
        float(row["close"]) > float(row[f"sma{sma}"])
        and float(row["ret126"]) > 0
    )


def candidate_set() -> list[Candidate]:
    def qqq_bh(rows):
        return {"QQQ": 1.0}

    def fixed_trend(rows, leverage, sma=200):
        return leverage_weights("QQQ", "TQQQ", leverage) if trend_ok(rows["QQQ"], sma) else {}

    def trend_ladder(rows):
        row = rows["QQQ"]
        if not trend_ok(row):
            return {}
        strong = (
            not pd.isna(row["sma50"])
            and not pd.isna(row["ret63"])
            and not pd.isna(row["vol20"])
            and float(row["close"]) > float(row["sma50"])
            and float(row["ret63"]) > 0
            and float(row["vol20"]) < 0.30
        )
        return leverage_weights("QQQ", "TQQQ", 2.0 if strong else 1.0)

    def adaptive(rows):
        row = rows["QQQ"]
        if pd.isna(row["sma200"]) or pd.isna(row["vol20"]):
            return {}
        if float(row["close"]) <= float(row["sma200"]):
            leverage = 0.5 if float(row["vol20"]) < 0.35 else 0.0
        else:
            strong = (
                not pd.isna(row["ret63"])
                and not pd.isna(row["sma50"])
                and float(row["ret63"]) > 0
                and float(row["close"]) > float(row["sma50"])
            )
            leverage = 2.0 if strong and float(row["vol20"]) < 0.30 else 1.0
        return leverage_weights("QQQ", "TQQQ", leverage)

    def vol_target(rows, target_vol, cap):
        row = rows["QQQ"]
        if not trend_ok(row) or pd.isna(row["vol20"]):
            return {}
        realized = max(float(row["vol20"]), 0.08)
        leverage = round(max(0.5, min(cap, target_vol / realized)) * 4) / 4
        return leverage_weights("QQQ", "TQQQ", leverage)

    def trend_vol(rows, target_vol, cap):
        row = rows["QQQ"]
        if pd.isna(row["sma200"]) or pd.isna(row["vol20"]):
            return {}
        if float(row["close"]) <= float(row["sma200"]):
            return {}
        if pd.isna(row["ret126"]) or float(row["ret126"]) <= 0:
            return {"QQQ": 1.0}
        leverage = round(
            max(1.0, min(cap, target_vol / max(float(row["vol20"]), 0.08))) * 4
        ) / 4
        return leverage_weights("QQQ", "TQQQ", leverage)

    def relative(rows, leverage):
        eligible = []
        for underlying, levered in (("QQQ", "TQQQ"), ("SOXX", "SOXL")):
            row = rows[underlying]
            if trend_ok(row) and not pd.isna(row["vol20"]):
                score = float(row["ret126"]) / max(float(row["vol20"]), 0.08)
                eligible.append((score, underlying, levered))
        if not eligible:
            return {}
        _, underlying, levered = max(eligible)
        return leverage_weights(underlying, levered, leverage)

    def relative_ladder(rows):
        eligible = []
        for underlying, levered in (("QQQ", "TQQQ"), ("SOXX", "SOXL")):
            row = rows[underlying]
            if trend_ok(row) and not pd.isna(row["vol20"]):
                score = float(row["ret126"]) / max(float(row["vol20"]), 0.08)
                eligible.append((score, underlying, levered, row))
        if not eligible:
            return {}
        _, underlying, levered, row = max(eligible, key=lambda item: item[0])
        strong = not pd.isna(row["ret63"]) and float(row["ret63"]) > 0 and float(row["vol20"]) < 0.35
        return leverage_weights(underlying, levered, 2.0 if strong else 1.0)

    def crash_reclaim(rows):
        row = rows["QQQ"]
        needed = ("sma50", "sma200", "ret21", "ret63", "dd252", "vol20")
        if any(pd.isna(row[key]) for key in needed):
            return {}
        close = float(row["close"])
        if close > float(row["sma200"]):
            leverage = 2.0 if float(row["ret63"]) > 0 and float(row["vol20"]) < 0.30 else 1.0
            return leverage_weights("QQQ", "TQQQ", leverage)
        recovery = (
            float(row["dd252"]) <= -0.15
            and close > float(row["sma50"])
            and float(row["ret21"]) > 0
        )
        return leverage_weights("QQQ", "TQQQ", 1.5) if recovery else {}

    return [
        Candidate("QQQ_BUY_HOLD", "benchmark", "once", qqq_bh),
        Candidate("TREND_1_5X_SMA200", "trend", "daily", lambda r: fixed_trend(r, 1.5)),
        Candidate("TREND_2X_SMA200", "trend", "daily", lambda r: fixed_trend(r, 2.0)),
        Candidate("TREND_2X_SMA150", "trend", "daily", lambda r: fixed_trend(r, 2.0, 150)),
        Candidate("TREND_LADDER_1X_2X", "trend", "daily", trend_ladder),
        Candidate("ADAPTIVE_0_5X_1X_2X", "trend", "daily", adaptive),
        Candidate("VOL_TARGET_20_CAP2", "volatility", "weekly", lambda r: vol_target(r, 0.20, 2.0)),
        Candidate("VOL_TARGET_25_CAP2_25", "volatility", "weekly", lambda r: vol_target(r, 0.25, 2.25)),
        Candidate("VOL_TARGET_30_CAP2_5", "volatility", "weekly", lambda r: vol_target(r, 0.30, 2.5)),
        Candidate("TREND_VOL_20_CAP2", "trend_vol", "weekly", lambda r: trend_vol(r, 0.20, 2.0)),
        Candidate("TREND_VOL_25_CAP2_5", "trend_vol", "weekly", lambda r: trend_vol(r, 0.25, 2.5)),
        Candidate("REL_MOM_1_5X", "relative_momentum", "weekly", lambda r: relative(r, 1.5)),
        Candidate("REL_MOM_2X", "relative_momentum", "weekly", lambda r: relative(r, 2.0)),
        Candidate("REL_MOM_LADDER", "relative_momentum", "weekly", relative_ladder),
        Candidate("CRASH_RECLAIM", "recovery", "daily", crash_reclaim),
    ]


def refresh_due(frequency: str, timestamp: pd.Timestamp, last_bucket: str | None):
    if frequency == "once":
        return last_bucket is None, "once"
    if frequency == "daily":
        return True, timestamp.date().isoformat()
    if frequency == "weekly":
        bucket = str(timestamp.to_period("W-FRI"))
        return bucket != last_bucket, bucket
    raise ValueError(frequency)


def rebalance(target, holdings, opens, cash, buy_fee, sell_fee, slippage, trades, timestamp):
    equity_open = cash + sum(holdings[s] * opens[s] * (1 - sell_fee) for s in holdings)
    target_qty = {}
    for symbol in holdings:
        weight = float(target.get(symbol, 0.0))
        price = opens[symbol] * (1 + slippage)
        target_qty[symbol] = math.floor(equity_open * weight / (price * (1 + buy_fee)))
    for symbol in holdings:
        difference = target_qty[symbol] - holdings[symbol]
        if difference >= 0:
            continue
        quantity = -difference
        price = opens[symbol] * (1 - slippage)
        fee = quantity * price * sell_fee
        cash += quantity * price - fee
        holdings[symbol] -= quantity
        trades.append((timestamp.date().isoformat(), symbol, "SELL", quantity))
    for symbol in holdings:
        difference = target_qty[symbol] - holdings[symbol]
        if difference <= 0:
            continue
        price = opens[symbol] * (1 + slippage)
        affordable = math.floor(cash / (price * (1 + buy_fee)))
        quantity = min(difference, affordable)
        if quantity <= 0:
            continue
        fee = quantity * price * buy_fee
        cash -= quantity * price + fee
        holdings[symbol] += quantity
        trades.append((timestamp.date().isoformat(), symbol, "BUY", quantity))
    return cash


def metrics(equity: pd.Series, exposures: list[float], trades: list[tuple]) -> dict[str, Any]:
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, 252)
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round((final / INITIAL_CAPITAL - 1) * 100, 2),
        "cagr_pct": round(((final / INITIAL_CAPITAL) ** (1 / years) - 1) * 100, 2),
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


def simulate(candidate, data, start, end, buy_fee, sell_fee, slippage):
    index = data["QQQ"].index
    for symbol in SYMBOLS[1:]:
        index = index.intersection(data[symbol].index)
    sessions = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if len(sessions) < 2 or prior.empty:
        raise ValueError(f"insufficient data {start} {end}")
    holdings = {symbol: 0 for symbol in SYMBOLS}
    cash = INITIAL_CAPITAL
    current_target = {}
    trades = []
    equity_values = []
    exposures = []
    prior_ts = prior[-1]
    rows = {symbol: data[symbol].loc[prior_ts] for symbol in SYMBOLS}
    pending = candidate.target(rows)
    _, last_bucket = refresh_due(candidate.frequency, prior_ts, None)
    for timestamp in sessions:
        opens = {symbol: float(data[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS}
        closes = {symbol: float(data[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS}
        if pending is not None and pending != current_target:
            if sum(pending.values()) > 1.000001:
                raise ValueError(f"weights exceed capital: {candidate.name} {pending}")
            cash = rebalance(pending, holdings, opens, cash, buy_fee, sell_fee, slippage, trades, timestamp)
            current_target = pending
        pending = None
        liquidation = sum(holdings[s] * closes[s] * (1 - sell_fee) for s in SYMBOLS)
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        refresh, bucket = refresh_due(candidate.frequency, timestamp, last_bucket)
        if refresh:
            rows = {symbol: data[symbol].loc[timestamp] for symbol in SYMBOLS}
            desired = candidate.target(rows)
            if desired != current_target:
                pending = desired
            last_bucket = bucket
    equity = pd.Series(equity_values, index=sessions)
    return metrics(equity, exposures, trades)


def rank_candidate(name, train, validation, qqq_train, qqq_validation):
    train_excess = float(train["cagr_pct"]) - float(qqq_train["cagr_pct"])
    validation_excess = float(validation["cagr_pct"]) - float(qqq_validation["cagr_pct"])
    validation_mdd_adv = float(validation["mdd_pct"]) - float(qqq_validation["mdd_pct"])
    sharpe_adv = float(validation["sharpe"]) - float(qqq_validation["sharpe"])
    eligible = train_excess > 0 and validation_excess > 0 and validation_mdd_adv >= -5
    score = min(train_excess, validation_excess) * 2 + validation_mdd_adv * 0.2 + sharpe_adv * 4
    return {
        "name": name,
        "eligible": eligible,
        "score": round(score, 4),
        "train_excess_cagr_pp": round(train_excess, 2),
        "validation_excess_cagr_pp": round(validation_excess, 2),
        "validation_mdd_advantage_pp": round(validation_mdd_adv, 2),
        "validation_sharpe_advantage": round(sharpe_adv, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    end = args.end or datetime.now(UTC).date().isoformat()
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=420)).isoformat()
    raw = {symbol: source.daily(symbol, warmup, end) for symbol in SYMBOLS}
    data = {symbol: features(raw[symbol]) for symbol in SYMBOLS}
    candidates = candidate_set()
    by_name = {candidate.name: candidate for candidate in candidates}
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)
    slippage = float(config.backtest.default_slippage)

    discovery_windows = {
        "train": ("2011-01-03", "2018-12-31"),
        "validation": ("2019-01-02", "2022-12-30"),
    }
    discovery = {}
    for candidate in candidates:
        discovery[candidate.name] = {}
        for label, (start, finish) in discovery_windows.items():
            result = simulate(candidate, data, start, finish, buy_fee, sell_fee, slippage)
            discovery[candidate.name][label] = result
            print(f"DISCOVERY {label:<10} {candidate.name:<25} CAGR={result['cagr_pct']:+6.2f}% MDD={result['mdd_pct']:6.2f}% Sharpe={result['sharpe']:.3f}")

    qqq_train = discovery["QQQ_BUY_HOLD"]["train"]
    qqq_validation = discovery["QQQ_BUY_HOLD"]["validation"]
    ranking = [
        rank_candidate(candidate.name, discovery[candidate.name]["train"], discovery[candidate.name]["validation"], qqq_train, qqq_validation)
        for candidate in candidates
        if candidate.name != "QQQ_BUY_HOLD"
    ]
    ranking.sort(key=lambda item: item["score"], reverse=True)
    locked = [item["name"] for item in ranking if item["eligible"]][:5]
    if not locked:
        locked = [item["name"] for item in ranking[:3]]
    print("LOCKED BEFORE OOS:", ", ".join(locked))

    confirmation_windows = {
        "oos_2023_plus": ("2023-01-03", end),
        "recent_2022_plus": ("2022-01-03", end),
        "full": ("2011-01-03", end),
    }
    confirmation = {}
    for name in ["QQQ_BUY_HOLD", *locked]:
        confirmation[name] = {}
        for label, (start, finish) in confirmation_windows.items():
            result = simulate(by_name[name], data, start, finish, buy_fee, sell_fee, slippage)
            confirmation[name][label] = result
            print(f"CONFIRM {label:<16} {name:<25} CAGR={result['cagr_pct']:+6.2f}% MDD={result['mdd_pct']:6.2f}% Sharpe={result['sharpe']:.3f}")

    qqq_full = confirmation["QQQ_BUY_HOLD"]["full"]
    final_rows = []
    for name in locked:
        full = confirmation[name]["full"]
        oos = confirmation[name]["oos_2023_plus"]
        recent = confirmation[name]["recent_2022_plus"]
        final_rows.append({
            "name": name,
            "family": by_name[name].family,
            "full_excess_cagr_pp": round(float(full["cagr_pct"]) - float(qqq_full["cagr_pct"]), 2),
            "full_mdd_advantage_pp": round(float(full["mdd_pct"]) - float(qqq_full["mdd_pct"]), 2),
            "oos": oos,
            "recent": recent,
            "full": full,
        })
    final_rows.sort(key=lambda row: (row["full_excess_cagr_pp"], row["full_mdd_advantage_pp"]), reverse=True)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end": end,
        "contract": {
            "initial_capital": INITIAL_CAPITAL,
            "buy_fee": buy_fee,
            "sell_fee": sell_fee,
            "slippage": slippage,
            "selection_uses_oos": False,
        },
        "discovery": discovery,
        "ranking": ranking,
        "locked_before_oos": locked,
        "confirmation": confirmation,
        "final_rows": final_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("===== FINAL =====")
    print(f"QQQ full CAGR={qqq_full['cagr_pct']:+.2f}% MDD={qqq_full['mdd_pct']:.2f}% Sharpe={qqq_full['sharpe']:.3f}")
    for row in final_rows:
        full = row["full"]
        print(f"{row['name']}: CAGR={full['cagr_pct']:+.2f}% MDD={full['mdd_pct']:.2f}% Sharpe={full['sharpe']:.3f} excess={row['full_excess_cagr_pp']:+.2f}pp")
    print(f"saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
