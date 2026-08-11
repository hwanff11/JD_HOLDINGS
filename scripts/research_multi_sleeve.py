"""Research-only time-diversified TQQQ/SOXL sleeve strategies.

Each entry is an independent sleeve.  Capital limits are portfolio percentages,
not fixed dollar limits per symbol.  Signals use completed bars and all fills use
the next session open.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from research_simple_strategies import ROOT, _idle_return, _research_indicators

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

SYMBOLS = ("TQQQ", "SOXL")
UNDERLYING = {"TQQQ": "QQQ", "SOXL": "SOXX"}
SEGMENTS = {
    "development_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "test_2023_present": ("2023-01-01", None),
    "full_history": ("2011-01-01", None),
}


@dataclass
class Sleeve:
    sleeve_id: int
    symbol: str
    signal: str
    quantity: int
    entry_price: float
    entry_fee: float
    entry_date: pd.Timestamp
    peak_close: float
    tp1_done: bool = False


@dataclass(frozen=True)
class Pending:
    action: str
    symbol: str
    sleeve_id: int | None = None
    signal: str = ""
    signal_close: float = 0.0


def _metrics(
    equity: pd.Series,
    trades: list[dict[str, Any]],
    exposure: list[float],
    idle: float,
    annual_days: int,
) -> dict[str, Any]:
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, annual_days)
    cycles = _cycles(trades)
    profits = sorted((float(c["net_pnl"]) for c in cycles), reverse=True)
    positive = sum(value for value in profits if value > 0)
    top = profits[0] if profits else 0.0
    return {
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "completed_sleeves": len(cycles),
        "win_rate_pct": (
            round(sum(c["net_pnl"] > 0 for c in cycles) / len(cycles) * 100, 2)
            if cycles
            else 0.0
        ),
        "net_pnl_excluding_best_trade": round(sum(profits[1:]), 2),
        "best_trade_profit_contribution_pct": round(top / positive * 100, 2) if positive else 0.0,
        "average_exposure_pct": round(sum(exposure) / len(exposure) * 100, 2),
        "idle_cash_income": round(idle, 2),
        "symbol_net_pnl": {
            symbol: round(sum(c["net_pnl"] for c in cycles if c["symbol"] == symbol), 2)
            for symbol in SYMBOLS
        },
        "annual_returns_pct": {
            str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
            for year, group in equity.groupby(equity.index.year)
        },
    }


def _cycles(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(int(trade["sleeve_id"]), []).append(trade)
    result = []
    for sleeve_id, rows in grouped.items():
        buys = [r for r in rows if r["side"] == "BUY"]
        sells = [r for r in rows if r["side"] == "SELL"]
        if not buys or sum(r["quantity"] for r in buys) != sum(r["quantity"] for r in sells):
            continue
        cost = sum(r["quantity"] * r["price"] + r["fee"] for r in buys)
        proceeds = sum(r["quantity"] * r["price"] - r["fee"] for r in sells)
        result.append({
            "sleeve_id": sleeve_id,
            "symbol": buys[0]["symbol"],
            "signal": buys[0]["signal"],
            "entry_date": buys[0]["date"],
            "exit_date": sells[-1]["date"],
            "net_pnl": round(proceeds - cost, 2),
        })
    return result


def _simulate(
    name: str,
    frames: dict[str, pd.DataFrame],
    idle_frame: pd.DataFrame,
    config: Any,
    *,
    start: str,
    end: str,
    slippage: float,
) -> dict[str, Any]:
    index = frames["TQQQ"].index
    for symbol in ("SOXL", "QQQ", "SOXX"):
        index = index.intersection(frames[symbol].index)
    index = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    index = index[index >= pd.Timestamp("2011-01-01")]
    initial = float(config.global_.capital_per_symbol) * len(config.enabled_symbols)
    cash = initial
    idle_income = 0.0
    sleeves: dict[int, Sleeve] = {}
    pending: list[Pending] = []
    trades: list[dict[str, Any]] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    next_id = 1
    last_entry = {symbol: -999 for symbol in SYMBOLS}
    idle_returns = _idle_return(idle_frame, index)
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)
    cash_buffer = float(config.idle_cash.cash_buffer)

    for session_no, timestamp in enumerate(index):
        prices = {symbol: frames[symbol].loc[timestamp] for symbol in SYMBOLS}
        bases = {symbol: frames[UNDERLYING[symbol]].loc[timestamp] for symbol in SYMBOLS}
        marked = sum(s.quantity * float(prices[s.symbol]["close"]) for s in sleeves.values())
        yieldable = max(0.0, cash - cash_buffer)
        income = yieldable * float(idle_returns.loc[timestamp])
        cash += income
        idle_income += income

        for order in pending:
            row = prices[order.symbol]
            fill = float(row["open"])
            if order.action == "BUY":
                equity_now = cash + sum(
                    s.quantity * float(prices[s.symbol]["open"])
                    for s in sleeves.values()
                )
                symbol_value = sum(
                    s.quantity * float(prices[s.symbol]["open"])
                    for s in sleeves.values()
                    if s.symbol == order.symbol
                )
                total_value = sum(
                    s.quantity * float(prices[s.symbol]["open"])
                    for s in sleeves.values()
                )
                symbol_sleeves = sum(
                    sleeve.symbol == order.symbol for sleeve in sleeves.values()
                )
                if name in {
                    "T_WINNER_PYRAMID",
                    "U_RELATIVE_PYRAMID",
                    "V_CRASH_RECLAIM_PYRAMID",
                }:
                    pyramid_weights = (0.10, 0.10, 0.15, 0.15)
                    slot_pct = pyramid_weights[min(symbol_sleeves, 3)]
                else:
                    slot_pct = 0.15
                budget = min(
                    equity_now * slot_pct,
                    max(0.0, equity_now * 0.60 - symbol_value),
                    max(0.0, equity_now * 0.75 - total_value),
                    cash - cash_buffer,
                )
                chase_ok = fill <= order.signal_close * (1 + float(config.global_.entry_max_chase_pct))
                quantity = math.floor(budget / (fill * (1 + slippage) * (1 + buy_fee))) if chase_ok else 0
                if quantity > 0:
                    price = fill * (1 + slippage)
                    fee = quantity * price * buy_fee
                    cash -= quantity * price + fee
                    sleeves[next_id] = Sleeve(
                        next_id,
                        order.symbol,
                        order.signal,
                        quantity,
                        price,
                        fee,
                        timestamp,
                        float(row["close"]),
                    )
                    trades.append(
                        {
                            "date": timestamp.date().isoformat(),
                            "sleeve_id": next_id,
                            "symbol": order.symbol,
                            "signal": order.signal,
                            "side": "BUY",
                            "quantity": quantity,
                            "price": round(price, 4),
                            "fee": round(fee, 2),
                        }
                    )
                    next_id += 1
            else:
                sleeve = sleeves.get(int(order.sleeve_id or 0))
                if sleeve is None:
                    continue
                quantity = (
                    sleeve.quantity // 2
                    if order.action == "TP1" and not sleeve.tp1_done
                    else sleeve.quantity
                )
                quantity = max(1, quantity)
                price = fill * (1 - slippage)
                fee = quantity * price * sell_fee
                cash += quantity * price - fee
                trades.append(
                    {
                        "date": timestamp.date().isoformat(),
                        "sleeve_id": sleeve.sleeve_id,
                        "symbol": sleeve.symbol,
                        "signal": sleeve.signal,
                        "side": "SELL",
                        "quantity": quantity,
                        "price": round(price, 4),
                        "fee": round(fee, 2),
                        "reason": order.action,
                    }
                )
                sleeve.quantity -= quantity
                if order.action == "TP1" and sleeve.quantity > 0:
                    sleeve.tp1_done = True
                if sleeve.quantity <= 0:
                    sleeves.pop(sleeve.sleeve_id, None)
        pending = []

        for sleeve in list(sleeves.values()):
            row, base = prices[sleeve.symbol], bases[sleeve.symbol]
            close = float(row["close"])
            sleeve.peak_close = max(sleeve.peak_close, close)
            held = (timestamp - sleeve.entry_date).days
            if not sleeve.tp1_done and close >= sleeve.entry_price * 1.06:
                pending.append(Pending("TP1", sleeve.symbol, sleeve.sleeve_id))
            elif sleeve.tp1_done and close <= sleeve.peak_close * 0.90:
                pending.append(Pending("TRAIL", sleeve.symbol, sleeve.sleeve_id))
            elif float(base["close"]) < float(base["sma50_r"]) or held >= 60:
                pending.append(Pending("EXIT", sleeve.symbol, sleeve.sleeve_id))

        for symbol in SYMBOLS:
            row, base = prices[symbol], bases[symbol]
            needed = ("close", "rsi2", "previous_high20")
            base_needed = ("close", "sma50_r", "sma200_r", "previous_high20")
            if any(pd.isna(row[key]) for key in needed) or any(pd.isna(base[key]) for key in base_needed):
                continue
            trend = (
                float(base["close"]) > float(base["sma200_r"])
                and float(base["sma50_r"]) > float(base["sma200_r"])
            )
            pullback = trend and float(row["rsi2"]) <= 10 and float(row["close"]) > float(row["open"])
            breakout = trend and float(base["close"]) > float(base["previous_high20"])
            open_for_symbol = [
                sleeve for sleeve in sleeves.values() if sleeve.symbol == symbol
            ]
            latest_entry = max(
                (sleeve.entry_price for sleeve in open_for_symbol), default=0.0
            )
            pyramid = bool(open_for_symbol) and float(row["close"]) >= latest_entry * 1.04
            reclaim = bool(base.get("reclaim20", False))
            eligible_scores = {
                candidate: (
                    float(bases[candidate]["return63"])
                    / max(float(bases[candidate]["vol20"]), 0.01)
                )
                for candidate in SYMBOLS
                if not pd.isna(bases[candidate]["return63"])
                and not pd.isna(bases[candidate]["vol20"])
                and float(bases[candidate]["close"])
                > float(bases[candidate]["sma200_r"])
                and float(bases[candidate]["sma50_r"])
                > float(bases[candidate]["sma200_r"])
            }
            relative_winner = (
                max(eligible_scores, key=eligible_scores.get)
                if eligible_scores
                else None
            )
            signal = ""
            if name in {"Q_PULLBACK_SLEEVES", "S_COMBINED_SLEEVES"} and pullback:
                signal = "PULLBACK"
            elif name in {"R_BREAKOUT_SLEEVES", "S_COMBINED_SLEEVES"} and breakout:
                signal = "BREAKOUT"
            elif name == "T_WINNER_PYRAMID" and (
                (not open_for_symbol and breakout) or pyramid
            ):
                signal = "INITIAL_BREAKOUT" if not open_for_symbol else "PYRAMID"
            elif name == "U_RELATIVE_PYRAMID" and symbol == relative_winner and (
                (not open_for_symbol and breakout) or pyramid
            ):
                signal = "RELATIVE_INITIAL" if not open_for_symbol else "PYRAMID"
            elif name == "V_CRASH_RECLAIM_PYRAMID" and (
                (not open_for_symbol and reclaim) or pyramid
            ):
                signal = "CRASH_RECLAIM" if not open_for_symbol else "PYRAMID"
            duplicate = any(
                p.action == "BUY" and p.symbol == symbol for p in pending
            )
            if signal and session_no - last_entry[symbol] >= 5 and not duplicate:
                pending.append(
                    Pending(
                        "BUY",
                        symbol,
                        signal=signal,
                        signal_close=float(row["close"]),
                    )
                )
                last_entry[symbol] = session_no

        marked = sum(s.quantity * float(prices[s.symbol]["close"]) * (1 - sell_fee) for s in sleeves.values())
        equity = cash + marked
        equity_values.append(equity)
        exposure_values.append(marked / equity if equity > 0 else 0.0)

    equity = pd.Series(equity_values, index=index, name=name)
    return {
        "metrics": _metrics(equity, trades, exposure_values, idle_income, config.backtest.annualization_days),
        "trades": trades,
        "completed_cycles": _cycles(trades),
        "open_sleeves": [
            vars(sleeve) | {"entry_date": sleeve.entry_date.date().isoformat()}
            for sleeve in sleeves.values()
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 다중 슬롯 전략 연구",
        "",
        f"- 생성시각: {report['generated_at']}",
        f"- 데이터 종료일: {report['end_date']}",
        "- 총 초기자금: $20,000",
        "- 고정 종목별 한도 없음; 단일 종목 60%, 전체 위험자산 75%",
        "- 일반 슬롯 15%; 피라미딩은 10%→10%→15%→15%",
        "",
        "| 후보 | 구간 | 누적수익 | CAGR | MDD | Sharpe | 완료슬롯 | "
        "최고거래 제외 손익 | 최고거래 기여도 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, candidate in report["candidates"].items():
        for segment, data in candidate.items():
            m = data["metrics"]
            lines.append(
                f"| {name} | {segment} | {m['total_return_pct']:+.2f}% | "
                f"{m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | "
                f"{m['sharpe']:.3f} | {m['completed_sleeves']} | "
                f"${m['net_pnl_excluding_best_trade']:,.2f} | "
                f"{m['best_trade_profit_contribution_pct']:.2f}% |"
            )
    lines.extend(
        [
            "",
            "## 피라미딩 후보",
            "",
            "- T: 각 기초자산 20일 돌파 후 수익 구간에서만 증액",
            "- U: QQQ·SOXX 위험조정 63일 모멘텀 1위만 진입·증액",
            "- V: 126일 고점 대비 12% 조정 후 20일선 회복 시 진입·증액",
            "",
            "> 연구 전용입니다. 운영 코드·strategy.yaml·Oracle·실주문을 "
            "변경하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "multi_sleeve.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports" / "multi_sleeve.md")
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)).isoformat()
    raw = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in (*SYMBOLS, "QQQ", "SOXX", config.idle_cash.symbol)
    }
    frames = {symbol: _research_indicators(frame) for symbol, frame in raw.items()}
    for symbol in ("QQQ", "SOXX"):
        frame = frames[symbol]
        frame["sma20_r"] = frame["close"].rolling(20, min_periods=20).mean()
        frame["high126_r"] = frame["close"].rolling(126, min_periods=126).max()
        drawdown = frame["close"] / frame["high126_r"] - 1
        frame["reclaim20"] = (
            (drawdown <= -0.12)
            & (frame["close"] > frame["sma20_r"])
            & (frame["close"].shift(1) <= frame["sma20_r"].shift(1))
            & (frame["sma50_r"] > frame["sma200_r"])
        )
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end_date": args.end,
        "slippage": args.slippage,
        "candidates": {},
    }
    for name in (
        "Q_PULLBACK_SLEEVES",
        "R_BREAKOUT_SLEEVES",
        "S_COMBINED_SLEEVES",
        "T_WINNER_PYRAMID",
        "U_RELATIVE_PYRAMID",
        "V_CRASH_RECLAIM_PYRAMID",
    ):
        report["candidates"][name] = {}
        for segment, (start, configured_end) in SEGMENTS.items():
            report["candidates"][name][segment] = _simulate(
                name,
                frames,
                raw[config.idle_cash.symbol],
                config,
                start=start,
                end=configured_end or args.end,
                slippage=args.slippage,
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
