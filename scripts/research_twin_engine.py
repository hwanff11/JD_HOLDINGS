"""Research-only monthly TQQQ/SOXL trend basket with SGOV ballast."""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from research_simple_strategies import ROOT, _idle_return

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


def _month_end_sessions(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
    values = pd.Series(index=index, data=index)
    return set(values.groupby(index.to_period("M")).last().tolist())


def _monthly_trend(frame: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    month_end = frame["close"].reindex(index).groupby(index.to_period("M")).last()
    moving_average = month_end.rolling(10, min_periods=10).mean()
    trend = month_end > moving_average
    result = pd.Series(False, index=index)
    end_sessions = _month_end_sessions(index)
    for timestamp in end_sessions:
        period = timestamp.to_period("M")
        result.loc[timestamp] = bool(trend.get(period, False))
    return result


def _metrics(
    equity: pd.Series,
    exposure: list[float],
    trades: list[dict[str, Any]],
    idle_income: float,
    annual_days: int,
) -> dict[str, Any]:
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, annual_days)
    return {
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "trade_fills": len(trades),
        "average_exposure_pct": round(sum(exposure) / len(exposure) * 100, 2),
        "idle_cash_income": round(idle_income, 2),
        "annual_returns_pct": {
            str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
            for year, group in equity.groupby(equity.index.year)
        },
    }


def _trade_to_target(
    symbol: str,
    target_weight: float,
    prices: dict[str, float],
    quantities: dict[str, int],
    cash: float,
    equity: float,
    *,
    timestamp: pd.Timestamp,
    buy_fee: float,
    sell_fee: float,
    slippage: float,
    trades: list[dict[str, Any]],
) -> float:
    current = quantities[symbol]
    raw_target = target_weight * equity
    buy_price = prices[symbol] * (1 + slippage)
    sell_price = prices[symbol] * (1 - slippage)
    target = math.floor(raw_target / (buy_price * (1 + buy_fee)))
    difference = target - current
    if difference > 0:
        affordable = math.floor(cash / (buy_price * (1 + buy_fee)))
        quantity = min(difference, affordable)
        if quantity <= 0:
            return cash
        fee = quantity * buy_price * buy_fee
        cash -= quantity * buy_price + fee
        side, price = "BUY", buy_price
    elif difference < 0:
        quantity = -difference
        fee = quantity * sell_price * sell_fee
        cash += quantity * sell_price - fee
        side, price = "SELL", sell_price
    else:
        return cash
    quantities[symbol] += quantity if side == "BUY" else -quantity
    trades.append(
        {
            "date": timestamp.date().isoformat(),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": round(price, 4),
            "fee": round(fee, 2),
            "target_weight": target_weight,
        }
    )
    return cash


def _simulate(
    name: str,
    raw: dict[str, pd.DataFrame],
    config: Any,
    *,
    start: str,
    end: str,
    slippage: float,
) -> dict[str, Any]:
    full_index = raw["TQQQ"].index
    for symbol in ("SOXL", "QQQ", "SOXX", config.idle_cash.symbol):
        full_index = full_index.intersection(raw[symbol].index)
    full_index = full_index[full_index <= pd.Timestamp(end)]
    index = full_index[full_index >= pd.Timestamp(start)]
    if len(index) < 2:
        raise ValueError(f"{name}: insufficient data")
    month_ends = _month_end_sessions(index)
    trend = {
        symbol: _monthly_trend(raw[UNDERLYING[symbol]], full_index)
        for symbol in SYMBOLS
    }
    initial = float(config.global_.capital_per_symbol) * len(config.enabled_symbols)
    cash = initial
    quantities = {symbol: 0 for symbol in SYMBOLS}
    active = {symbol: False for symbol in SYMBOLS}
    pending_targets: dict[str, float] | None = None
    trades: list[dict[str, Any]] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    idle_income = 0.0
    idle_returns = _idle_return(raw[config.idle_cash.symbol], index)
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)
    target = 0.20 if name == "W_TWIN_ENGINE_20" else 0.25

    for timestamp in index:
        open_prices = {
            symbol: float(raw[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS
        }
        close_prices = {
            symbol: float(raw[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS
        }
        income = max(0.0, cash - float(config.idle_cash.cash_buffer)) * float(
            idle_returns.loc[timestamp]
        )
        cash += income
        idle_income += income

        if pending_targets is not None:
            open_equity = cash + sum(
                quantities[symbol] * open_prices[symbol] for symbol in SYMBOLS
            )
            for symbol in SYMBOLS:
                if pending_targets[symbol] < 0.001:
                    cash = _trade_to_target(
                        symbol,
                        0.0,
                        open_prices,
                        quantities,
                        cash,
                        open_equity,
                        timestamp=timestamp,
                        buy_fee=buy_fee,
                        sell_fee=sell_fee,
                        slippage=slippage,
                        trades=trades,
                    )
            open_equity = cash + sum(
                quantities[symbol] * open_prices[symbol] for symbol in SYMBOLS
            )
            for symbol in SYMBOLS:
                if pending_targets[symbol] > 0:
                    cash = _trade_to_target(
                        symbol,
                        pending_targets[symbol],
                        open_prices,
                        quantities,
                        cash,
                        open_equity,
                        timestamp=timestamp,
                        buy_fee=buy_fee,
                        sell_fee=sell_fee,
                        slippage=slippage,
                        trades=trades,
                    )
            pending_targets = None

        close_equity = cash + sum(
            quantities[symbol] * close_prices[symbol] for symbol in SYMBOLS
        )
        if timestamp in month_ends:
            active = {symbol: bool(trend[symbol].loc[timestamp]) for symbol in SYMBOLS}
            pending_targets = {
                symbol: target if active[symbol] else 0.0 for symbol in SYMBOLS
            }
        elif name == "Y_TWIN_ENGINE_BAND25":
            weights = {
                symbol: quantities[symbol] * close_prices[symbol] / close_equity
                for symbol in SYMBOLS
            }
            if any(
                active[symbol]
                and (weights[symbol] >= 0.32 or weights[symbol] <= 0.18)
                for symbol in SYMBOLS
            ):
                pending_targets = {
                    symbol: 0.25 if active[symbol] else 0.0 for symbol in SYMBOLS
                }

        liquidation = sum(
            quantities[symbol] * close_prices[symbol] * (1 - sell_fee)
            for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposure_values.append(liquidation / equity if equity > 0 else 0.0)

    equity = pd.Series(equity_values, index=index, name=name)
    return {
        "metrics": _metrics(
            equity,
            exposure_values,
            trades,
            idle_income,
            config.backtest.annualization_days,
        ),
        "trades": trades,
        "open_positions": quantities,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 월간 쌍발엔진 연구",
        "",
        f"- 생성시각: {report['generated_at']}",
        f"- 데이터 종료일: {report['end_date']}",
        "- QQQ·SOXX 월말 종가가 각 10개월 이동평균 위일 때만 투자",
        "- 나머지 자금 SGOV, 다음 거래일 시가 체결",
        "",
        "| 후보 | 구간 | 누적수익 | CAGR | MDD | Sharpe | 평균노출 | 체결 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, candidate in report["candidates"].items():
        for segment, data in candidate.items():
            metrics = data["metrics"]
            lines.append(
                f"| {name} | {segment} | {metrics['total_return_pct']:+.2f}% | "
                f"{metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% | "
                f"{metrics['sharpe']:.3f} | {metrics['average_exposure_pct']:.2f}% | "
                f"{metrics['trade_fills']} |"
            )
    lines.extend(
        [
            "",
            "- W: 조건 충족 종목당 20% 월간 리밸런싱",
            "- X: 조건 충족 종목당 25% 월간 리밸런싱",
            "- Y: X에 일중 비중 18%·32% 밴드 리밸런싱 추가",
            "",
            "> 연구 전용이며 운영 코드·설정·Oracle·실주문을 변경하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "twin_engine.json"
    )
    parser.add_argument(
        "--markdown", type=Path, default=ROOT / "reports" / "twin_engine.md"
    )
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=500)
    ).isoformat()
    raw = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in (*SYMBOLS, "QQQ", "SOXX", config.idle_cash.symbol)
    }
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end_date": args.end,
        "slippage": args.slippage,
        "candidates": {},
    }
    for name in ("W_TWIN_ENGINE_20", "X_TWIN_ENGINE_25", "Y_TWIN_ENGINE_BAND25"):
        report["candidates"][name] = {}
        for segment, (start, configured_end) in SEGMENTS.items():
            report["candidates"][name][segment] = _simulate(
                name,
                raw,
                config,
                start=start,
                end=configured_end or args.end,
                slippage=args.slippage,
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
