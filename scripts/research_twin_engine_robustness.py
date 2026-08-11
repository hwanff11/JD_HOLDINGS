"""Robustness checks for the research-only monthly twin-engine strategy."""

# ruff: noqa: E501, I001

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from research_simple_strategies import ROOT, _idle_return
from research_twin_engine import SYMBOLS, UNDERLYING, _metrics, _month_end_sessions
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

WINDOWS = tuple((year, year + 4) for year in range(2011, 2022))
STRESS = {
    "2011_debt_crisis": ("2011-07-01", "2011-12-31"),
    "2015_2016_growth_scare": ("2015-06-01", "2016-03-31"),
    "2018_q4": ("2018-09-01", "2018-12-31"),
    "2020_covid": ("2020-02-01", "2020-06-30"),
    "2022_rates": ("2022-01-01", "2022-12-31"),
}


def _trend(frame: pd.DataFrame, index: pd.DatetimeIndex, months: int) -> pd.Series:
    monthly = frame["close"].reindex(index).groupby(index.to_period("M")).last()
    signal = monthly > monthly.rolling(months, min_periods=months).mean()
    result = pd.Series(False, index=index)
    for timestamp in _month_end_sessions(index):
        result.loc[timestamp] = bool(signal.get(timestamp.to_period("M"), False))
    return result


def _slice_metrics(equity: pd.Series, annual_days: int) -> dict[str, float]:
    if len(equity) < 2:
        return {}
    exposure = [0.0] * len(equity)
    return _metrics(equity, exposure, [], 0.0, annual_days)


def _simulate(
    raw: dict[str, pd.DataFrame],
    config: Any,
    *,
    end: str,
    ma_months: int,
    delay: int,
    enabled: tuple[str, ...] = SYMBOLS,
    slippage: float = 0.001,
    monthly_rebalance: bool = True,
    include_details: bool = False,
) -> tuple[pd.Series, dict[str, Any]]:
    full_index = raw["TQQQ"].index
    for symbol in ("SOXL", "QQQ", "SOXX"):
        full_index = full_index.intersection(raw[symbol].index)
    index = full_index[
        (full_index >= pd.Timestamp("2011-01-01")) & (full_index <= pd.Timestamp(end))
    ]
    month_ends = _month_end_sessions(index)
    signals = {
        symbol: _trend(raw[UNDERLYING[symbol]], full_index, ma_months)
        for symbol in SYMBOLS
    }
    initial = float(config.global_.capital_per_symbol) * len(config.enabled_symbols)
    cash = initial
    quantities = {symbol: 0 for symbol in SYMBOLS}
    scheduled: dict[int, dict[str, float]] = {}
    previous_targets = {symbol: 0.0 for symbol in SYMBOLS}
    equity_values: list[float] = []
    exposure_values: list[float] = []
    trades: list[dict[str, Any]] = []
    idle_income = 0.0
    idle_returns = _idle_return(raw[config.idle_cash.symbol], index)
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)

    for position, timestamp in enumerate(index):
        opens = {symbol: float(raw[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS}
        closes = {symbol: float(raw[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS}
        income = max(0.0, cash - float(config.idle_cash.cash_buffer)) * float(
            idle_returns.loc[timestamp]
        )
        cash += income
        idle_income += income

        targets = scheduled.pop(position, None)
        if targets is not None:
            open_equity = cash + sum(quantities[s] * opens[s] for s in SYMBOLS)
            for symbol in SYMBOLS:
                target_weight = targets[symbol]
                buy_price = opens[symbol] * (1 + slippage)
                sell_price = opens[symbol] * (1 - slippage)
                target_qty = math.floor(
                    target_weight * open_equity / (buy_price * (1 + buy_fee))
                )
                difference = target_qty - quantities[symbol]
                if difference > 0:
                    quantity = min(
                        difference, math.floor(cash / (buy_price * (1 + buy_fee)))
                    )
                    if quantity > 0:
                        fee = quantity * buy_price * buy_fee
                        cash -= quantity * buy_price + fee
                        quantities[symbol] += quantity
                        trades.append({"date": str(timestamp.date()), "symbol": symbol, "side": "BUY", "quantity": quantity, "price": round(buy_price, 6), "fee": round(fee, 6), "target_weight": target_weight})
                elif difference < 0:
                    quantity = -difference
                    fee = quantity * sell_price * sell_fee
                    cash += quantity * sell_price - fee
                    quantities[symbol] -= quantity
                    trades.append({"date": str(timestamp.date()), "symbol": symbol, "side": "SELL", "quantity": quantity, "price": round(sell_price, 6), "fee": round(fee, 6), "target_weight": target_weight})

        if timestamp in month_ends:
            targets = {
                symbol: (
                    0.15
                    if symbol in enabled and signals[symbol].loc[timestamp]
                    else 0.0
                )
                for symbol in SYMBOLS
            }
            due = position + 1 + delay
            if due < len(index) and (
                monthly_rebalance or targets != previous_targets
            ):
                scheduled[due] = targets
            previous_targets = targets

        liquidation = sum(
            quantities[s] * closes[s] * (1 - sell_fee) for s in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposure_values.append(liquidation / equity if equity > 0 else 0.0)

    equity = pd.Series(equity_values, index=index)
    metrics = _metrics(
        equity, exposure_values, trades, idle_income, config.backtest.annualization_days
    )
    if include_details:
        metrics["_trades"] = trades
        metrics["_open_positions"] = quantities
        metrics["_final_cash"] = round(cash, 2)
    return equity, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "twin_engine_robustness.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports" / "twin_engine_robustness.md")
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=800)).isoformat()
    raw = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in (*SYMBOLS, "QQQ", "SOXX", config.idle_cash.symbol)
    }

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end_date": args.end,
        "slippage": args.slippage,
        "sensitivity": {},
        "delays": {},
        "engines": {},
        "rolling_5y": {},
        "stress": {},
    }
    annual_days = config.backtest.annualization_days
    for months in range(8, 13):
        equity, metrics = _simulate(raw, config, end=args.end, ma_months=months, delay=0, slippage=args.slippage)
        report["sensitivity"][str(months)] = metrics
        if months == 10:
            base_equity = equity
    for delay in range(4):
        _, metrics = _simulate(raw, config, end=args.end, ma_months=10, delay=delay, slippage=args.slippage)
        report["delays"][str(delay)] = metrics
    for label, enabled in {"TQQQ_only": ("TQQQ",), "SOXL_only": ("SOXL",), "both": SYMBOLS}.items():
        _, metrics = _simulate(raw, config, end=args.end, ma_months=10, delay=0, enabled=enabled, slippage=args.slippage)
        report["engines"][label] = metrics
    for start_year, end_year in WINDOWS:
        section = base_equity[
            (base_equity.index >= pd.Timestamp(f"{start_year}-01-01"))
            & (base_equity.index <= pd.Timestamp(f"{end_year}-12-31"))
        ]
        report["rolling_5y"][f"{start_year}_{end_year}"] = _slice_metrics(section, annual_days)
    for label, (start, end) in STRESS.items():
        section = base_equity[
            (base_equity.index >= pd.Timestamp(start))
            & (base_equity.index <= pd.Timestamp(end))
        ]
        report["stress"][label] = _slice_metrics(section, annual_days)

    lines = [
        "# 월간 쌍발엔진 강건성 검증", "",
        "## 이동평균 민감도", "",
        "| 개월 | 누적수익 | MDD | Sharpe |", "|---:|---:|---:|---:|",
    ]
    for months, m in report["sensitivity"].items():
        lines.append(f"| {months} | {m['total_return_pct']:+.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} |")
    lines.extend(["", "## 월말 신호 후 추가 체결 지연", "", "| 추가 지연 | 누적수익 | MDD | Sharpe |", "|---:|---:|---:|---:|"])
    for delay, m in report["delays"].items():
        lines.append(f"| {delay}일 | {m['total_return_pct']:+.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} |")
    lines.extend(["", "## 엔진별 독립 성과", "", "| 구성 | 누적수익 | MDD | Sharpe |", "|---|---:|---:|---:|"])
    for label, m in report["engines"].items():
        lines.append(f"| {label} | {m['total_return_pct']:+.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} |")
    positive_windows = sum(m.get("total_return_pct", 0) > 0 for m in report["rolling_5y"].values())
    lines.extend(["", f"- 5년 순환구간 흑자: {positive_windows}/{len(WINDOWS)}", "", "> 연구 전용이며 운영 코드·설정·Oracle·실주문을 변경하지 않습니다."])
    markdown = "\n".join(lines) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
